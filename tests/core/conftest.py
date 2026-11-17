import collections.abc
import pathlib
import typing as t

import django.conf
import django.db.utils
import pytest
import pytest_django

from dj_turso import base


# Per-worker database files: two processes cannot open one Turso file.
@pytest.fixture(scope="session")
def django_db_modify_db_settings(tmp_path_factory: pytest.TempPathFactory) -> None:
    directory = tmp_path_factory.mktemp("databases")
    django.conf.settings.DATABASES["default"] = {
        "ENGINE": "dj_turso",
        "NAME": str(directory / "default.db"),
        "TEST": {"NAME": str(directory / "test_default.db")},
    }


@pytest.fixture(params=[pytest.param("pyturso", id="pyturso")])
def target(
    django_db_blocker: pytest_django.DjangoDbBlocker, tmp_path: pathlib.Path
) -> collections.abc.Iterator[base.DatabaseWrapper]:
    handler = django.db.utils.ConnectionHandler(
        {"default": {"ENGINE": "dj_turso", "NAME": str(tmp_path / "default.db")}}
    )
    connection = t.cast("base.DatabaseWrapper", handler["default"])
    with django_db_blocker.unblock():
        yield connection
        connection.close()
