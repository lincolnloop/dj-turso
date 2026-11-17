"""Test-run setup replicating Django's runtests.py for the vendored apps."""

import collections.abc
import functools
import gc
import os
import pathlib
import tempfile

import django.conf
import django.db
import pytest
from django.db.backends.base import base
from pytest_django import plugin

from scripts import vendor_django

collect_ignore = [
    f"django/{name}"
    for name in vendor_django.read_app_list(
        pathlib.Path(__file__).with_name("apps.txt")
    )["support"]
]


def pytest_configure(config: pytest.Config) -> None:
    config.add_cleanup(functools.partial(gc.set_threshold, *gc.get_threshold()))
    gc.set_threshold(100_000)
    monkeypatch = pytest.MonkeyPatch()
    config.add_cleanup(monkeypatch.undo)
    # Makes test database creation apply the backend's django_test_skips and
    # django_test_expected_failures, as runtests.py does.
    monkeypatch.setenv("RUNNING_DJANGOS_TEST_SUITE", "true")


# runtests.py never blocks the database: skipUnlessDBFeature probes it at collection
# and plain unittest classes query it. pytest-django's unconfigure undoes this.
def pytest_sessionstart(session: pytest.Session) -> None:
    session.config.stash[plugin.blocking_manager_key].unblock()


@pytest.hookimpl(wrapper=True)
def pytest_collection(session: pytest.Session) -> collections.abc.Generator[None]:
    yield
    # sqlite's close() keeps in-memory connections open, so one opened by a
    # collection-time probe would shadow the test database created later.
    for connection in django.db.connections.all(initialized_only=True):
        base.BaseDatabaseWrapper.close(connection)


# Turso test databases are files, and two processes cannot open one: each xdist
# worker gets its own directory. In place: collection already configured them.
@pytest.fixture(scope="session")
def django_db_modify_db_settings(tmp_path_factory: pytest.TempPathFactory) -> None:
    if os.environ.get("DJ_TURSO_TARGET", "pyturso") == "sqlite":
        return
    directory = tmp_path_factory.mktemp("databases")
    for connection in django.db.connections.all():
        connection.settings_dict["TEST"]["NAME"] = str(
            directory / f"test_{connection.alias}.db"
        )


# runtests.py creates the test databases before any test runs; plain unittest
# classes rely on that, whichever xdist worker they land on.
@pytest.fixture(autouse=True, scope="session")
def _runtests_databases(django_db_setup: None) -> None:
    pass


@pytest.fixture(autouse=True, scope="session")
def _runtests_temporary_directory(
    tmp_path_factory: pytest.TempPathFactory,
) -> collections.abc.Iterator[None]:
    temporary_directory = tmp_path_factory.mktemp("django_")
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("TMPDIR", str(temporary_directory))
        monkeypatch.setattr(tempfile, "tempdir", str(temporary_directory))
        monkeypatch.setattr(
            django.conf.settings, "STATIC_ROOT", str(temporary_directory / "static")
        )
        yield
