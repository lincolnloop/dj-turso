import collections.abc
import errno
import multiprocessing
import os
import pathlib
import re
import subprocess
import sys
import typing as t

import django.db
import django.db.utils
import pytest
import pytest_django
import turso
from django.db.backends.base import base
from django.test import runner

from tests import utils
from tests.core import models

REPOSITORY_ROOT = pathlib.Path(__file__).parents[2]

RegisterDatabase = collections.abc.Callable[
    [str, dict[str, t.Any]], base.BaseDatabaseWrapper
]


@pytest.fixture
def register_database(
    django_db_blocker: pytest_django.DjangoDbBlocker,
    settings: pytest_django.Settings,
) -> collections.abc.Iterator[RegisterDatabase]:
    aliases: list[str] = []
    original = django.db.connections.settings

    def register(
        alias: str, settings_dict: dict[str, t.Any]
    ) -> base.BaseDatabaseWrapper:
        settings.DATABASES = {**settings.DATABASES, alias: settings_dict}
        # The handler reads DATABASES once and ignores later overrides.
        django.db.connections.settings = django.db.connections.configure_settings(
            settings.DATABASES
        )
        aliases.append(alias)
        return django.db.connections[alias]

    with django_db_blocker.unblock():
        yield register
        for alias in aliases:
            django.db.connections[alias].close()
            del django.db.connections[alias]
        django.db.connections.settings = original


@pytest.fixture
def start_method(request: pytest.FixtureRequest) -> collections.abc.Iterator[str]:
    original = multiprocessing.get_start_method()
    multiprocessing.set_start_method(request.param, force=True)
    yield request.param
    multiprocessing.set_start_method(original, force=True)


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("default.db", id="file"),
        pytest.param("https://example.invalid", id="url"),
    ],
)
def test_test_database_defaults_to_memory(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    register_database: RegisterDatabase,
    tmp_path: pathlib.Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    connection = register_database("creation", {"ENGINE": "dj_turso", "NAME": name})

    test_name = connection.creation.create_test_db(verbosity=0)
    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM sqlite_master WHERE name = 'core_tag'")
        rows = cursor.fetchall()
    connection.creation.destroy_test_db(name, verbosity=0)

    assert test_name == ":memory:"
    assert rows == [("core_tag",)]
    assert sorted(path.name for path in tmp_path.iterdir()) == []


def test_explicit_test_name_is_used(
    register_database: RegisterDatabase, tmp_path: pathlib.Path
) -> None:
    connection = register_database(
        "creation",
        {
            "ENGINE": "dj_turso",
            "NAME": str(tmp_path / "default.db"),
            "TEST": {"NAME": str(tmp_path / "explicit.db")},
        },
    )

    test_name = connection.creation.create_test_db(verbosity=0)
    listing = sorted(path.name for path in tmp_path.iterdir())
    connection.creation.destroy_test_db(str(tmp_path / "default.db"), verbosity=0)

    assert test_name == str(tmp_path / "explicit.db")
    assert listing == ["explicit.db", "explicit.db-wal"]


def test_in_memory_name_tests_in_memory(register_database: RegisterDatabase) -> None:
    connection = register_database(
        "creation", {"ENGINE": "dj_turso", "NAME": ":memory:"}
    )

    test_name = connection.creation.create_test_db(verbosity=0)
    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM sqlite_master WHERE name = 'core_tag'")
        rows = cursor.fetchall()
    connection.creation.destroy_test_db(":memory:", verbosity=0)

    assert test_name == ":memory:"
    assert rows == [("core_tag",)]


def test_aliases_get_separate_test_databases(
    register_database: RegisterDatabase,
) -> None:
    first = register_database("first", {"ENGINE": "dj_turso", "NAME": "first.db"})
    second = register_database("second", {"ENGINE": "dj_turso", "NAME": "second.db"})
    first.creation.create_test_db(verbosity=0)
    second.creation.create_test_db(verbosity=0)

    models.Tag.objects.using("first").create(name="first")
    models.Tag.objects.using("second").create(name="second")
    first_names = list(models.Tag.objects.using("first").values_list("name", flat=True))
    second_names = list(
        models.Tag.objects.using("second").values_list("name", flat=True)
    )
    first.creation.destroy_test_db("first.db", verbosity=0)
    second.creation.destroy_test_db("second.db", verbosity=0)

    assert first_names == ["first"]
    assert second_names == ["second"]


def test_create_replaces_existing_test_database(
    register_database: RegisterDatabase, tmp_path: pathlib.Path
) -> None:
    stale = turso.connect(str(tmp_path / "test_default.db"), isolation_level=None)
    stale.execute("CREATE TABLE stale (name TEXT)")
    stale.close()
    connection = register_database(
        "creation",
        {
            "ENGINE": "dj_turso",
            "NAME": str(tmp_path / "default.db"),
            "TEST": {"NAME": str(tmp_path / "test_default.db")},
        },
    )

    connection.creation.create_test_db(verbosity=0, autoclobber=True)
    table_names = connection.introspection.table_names()
    connection.creation.destroy_test_db(str(tmp_path / "default.db"), verbosity=0)

    assert table_names == [
        "core_attachment",
        "core_counter",
        "core_event",
        "core_product",
        "core_tag",
    ]


def test_parallel_setup_clones_file_test_database(
    register_database: RegisterDatabase, tmp_path: pathlib.Path
) -> None:
    connection = register_database(
        "creation",
        {
            "ENGINE": "dj_turso",
            "NAME": str(tmp_path / "default.db"),
            "TEST": {"DEPENDENCIES": [], "NAME": str(tmp_path / "test_default.db")},
        },
    )
    connection.creation.create_test_db(verbosity=0)
    models.Tag.objects.using("creation").create(name="kept")
    connection.creation.destroy_test_db(
        str(tmp_path / "default.db"), verbosity=0, keepdb=True
    )
    test_runner = runner.DiscoverRunner(
        interactive=False, keepdb=True, parallel=2, verbosity=0
    )

    old_config = test_runner.setup_databases(aliases={"creation"})
    first_names = get_tag_names(tmp_path / "test_default_1.db")
    second_names = get_tag_names(tmp_path / "test_default_2.db")
    test_runner.teardown_databases(old_config)

    assert first_names == ["kept"]
    assert second_names == ["kept"]


def test_parallel_setup_replaces_existing_clone(
    capsys: pytest.CaptureFixture[str],
    register_database: RegisterDatabase,
    tmp_path: pathlib.Path,
) -> None:
    stale = turso.connect(str(tmp_path / "test_default_1.db"), isolation_level=None)
    stale.execute("CREATE TABLE stale (name TEXT)")
    stale.close()
    register_database(
        "creation",
        {
            "ENGINE": "dj_turso",
            "NAME": str(tmp_path / "default.db"),
            "TEST": {"DEPENDENCIES": [], "NAME": str(tmp_path / "test_default.db")},
        },
    )
    test_runner = runner.DiscoverRunner(interactive=False, parallel=2, verbosity=1)

    old_config = test_runner.setup_databases(aliases={"creation"})
    table_names = get_table_names(tmp_path / "test_default_1.db")
    test_runner.teardown_databases(old_config)
    listing = sorted(path.name for path in tmp_path.iterdir())

    assert table_names == [
        "core_attachment",
        "core_counter",
        "core_event",
        "core_product",
        "core_tag",
    ]
    assert listing == []
    assert capsys.readouterr().err == (
        "Creating test database for alias 'creation'...\n"
        "Destroying old test database for alias 'creation'...\n"
        "Cloning test database for alias 'creation'...\n"
        "Destroying old test database for alias 'creation'...\n"
        "Cloning test database for alias 'creation'...\n"
        "Destroying test database for alias 'creation'...\n"
        "Destroying test database for alias 'creation'...\n"
        "Destroying test database for alias 'creation'...\n"
    )


def test_parallel_setup_exits_when_existing_clone_cannot_be_deleted(
    capsys: pytest.CaptureFixture[str],
    register_database: RegisterDatabase,
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "test_default_1.db").mkdir()
    connection = register_database(
        "creation",
        {
            "ENGINE": "dj_turso",
            "NAME": str(tmp_path / "default.db"),
            "TEST": {"DEPENDENCIES": [], "NAME": str(tmp_path / "test_default.db")},
        },
    )
    # Unlinking a directory fails with a different error per platform.
    error_number = errno.EISDIR if sys.platform == "linux" else errno.EPERM

    with pytest.raises(SystemExit) as excinfo:
        runner.DiscoverRunner(
            interactive=False, parallel=2, verbosity=0
        ).setup_databases(aliases={"creation"})
    connection.creation.destroy_test_db(str(tmp_path / "default.db"), verbosity=0)

    assert excinfo.value.code == 2
    assert capsys.readouterr().err == (
        "Got an error deleting the old test database: "
        f"[Errno {error_number}] {os.strerror(error_number)}: "
        f"'{tmp_path / 'test_default_1.db'}'\n"
    )


def test_parallel_setup_keeps_existing_clone_with_keepdb(
    register_database: RegisterDatabase, tmp_path: pathlib.Path
) -> None:
    stale = turso.connect(str(tmp_path / "test_default_1.db"), isolation_level=None)
    stale.execute("CREATE TABLE stale (name TEXT)")
    stale.close()
    register_database(
        "creation",
        {
            "ENGINE": "dj_turso",
            "NAME": str(tmp_path / "default.db"),
            "TEST": {"DEPENDENCIES": [], "NAME": str(tmp_path / "test_default.db")},
        },
    )
    test_runner = runner.DiscoverRunner(
        interactive=False, keepdb=True, parallel=2, verbosity=0
    )

    old_config = test_runner.setup_databases(aliases={"creation"})
    table_names = get_table_names(tmp_path / "test_default_1.db")
    test_runner.teardown_databases(old_config)

    assert table_names == ["stale"]


@pytest.mark.parametrize(
    ("start_method", "worker_database_name"),
    [
        pytest.param("fork", ":memory:", id="fork"),
        pytest.param("forkserver", "creation_1.sqlite3", id="forkserver"),
        pytest.param("spawn", "creation_1.sqlite3", id="spawn"),
    ],
    indirect=["start_method"],
)
@pytest.mark.usefixtures("start_method")
def test_parallel_worker_opens_memory_test_database_clone(
    monkeypatch: pytest.MonkeyPatch,
    register_database: RegisterDatabase,
    tmp_path: pathlib.Path,
    worker_database_name: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    connection = register_database(
        "creation",
        {"ENGINE": "dj_turso", "NAME": "default.db", "TEST": {"DEPENDENCIES": []}},
    )
    test_runner = runner.DiscoverRunner(interactive=False, parallel=2, verbosity=0)

    old_config = test_runner.setup_databases(aliases={"creation"})
    # A worker sets up in its own process: give the parent its settings back.
    parent_settings = dict(connection.settings_dict)
    connection.creation.setup_worker_connection(1)
    name = connection.settings_dict["NAME"]
    table_names = connection.introspection.table_names()
    connection.close()
    connection.settings_dict.update(parent_settings)
    test_runner.teardown_databases(old_config)
    listing = sorted(path.name for path in tmp_path.iterdir())

    assert name == worker_database_name
    assert table_names == [
        "core_attachment",
        "core_counter",
        "core_event",
        "core_product",
        "core_tag",
    ]
    assert listing == []


def test_clone_refuses_url_test_database(register_database: RegisterDatabase) -> None:
    connection = register_database(
        "creation", {"ENGINE": "dj_turso", "NAME": "https://example.invalid"}
    )

    with pytest.raises(NotImplementedError) as excinfo:
        connection.creation.clone_test_db(suffix="1", verbosity=0)

    assert str(excinfo.value) == (
        "The database backend doesn't support cloning databases. "
        "Disable the option to run tests in parallel processes."
    )


@pytest.mark.parametrize(
    "start_method",
    [
        pytest.param("fork", id="fork"),
        pytest.param("forkserver", id="forkserver"),
        pytest.param("spawn", id="spawn"),
    ],
)
def test_parallel_run_gives_each_worker_a_clone(
    start_method: str, tmp_path: pathlib.Path
) -> None:
    child = (
        "import multiprocessing, django, django.core.management;"
        f"multiprocessing.set_start_method({start_method!r});"
        "django.setup();"
        "django.core.management.call_command("
        "'test', 'tests.core.parallel_cases', parallel=2)"
    )

    completed = subprocess.run(
        [sys.executable, "-c", child],
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "tests.core.settings",
            "PYTHONPATH": str(REPOSITORY_ROOT),
        },
        text=True,
    )

    assert completed.returncode == 0
    assert re.sub(r"in [0-9.]+s", "in 0s", completed.stderr) == (
        "Creating test database for alias 'default'...\n"
        "Cloning test database for alias 'default'...\n"
        "Cloning test database for alias 'default'...\n"
        "..\n"
        "----------------------------------------------------------------------\n"
        "Ran 2 tests in 0s\n"
        "\n"
        "OK\n"
        "Destroying test database for alias 'default'...\n"
        "Destroying test database for alias 'default'...\n"
        "Destroying test database for alias 'default'...\n"
    )


def test_url_name_tests_on_local_test_name(
    register_database: RegisterDatabase, tmp_path: pathlib.Path
) -> None:
    connection = register_database(
        "creation",
        {
            "ENGINE": "dj_turso",
            "NAME": "https://example.invalid",
            "OPTIONS": {"auth_token": "remote-only"},
            "TEST": {"NAME": str(tmp_path / "test_remote.db")},
        },
    )

    test_name = connection.creation.create_test_db(verbosity=0)
    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM sqlite_master WHERE name = 'core_tag'")
        rows = cursor.fetchall()
    connection.creation.destroy_test_db("https://example.invalid", verbosity=0)

    assert test_name == str(tmp_path / "test_remote.db")
    assert rows == [("core_tag",)]
    assert sorted(path.name for path in tmp_path.iterdir()) == []


def test_url_test_name_is_left_in_place(
    register_database: RegisterDatabase, sqld_url: str
) -> None:
    connection = register_database(
        "creation",
        {
            "ENGINE": "dj_turso",
            "NAME": sqld_url,
            "OPTIONS": {"auth_token": utils.SQLD_JWT},
            "TEST": {"NAME": sqld_url},
        },
    )

    test_name = connection.creation.create_test_db(verbosity=0)
    connection.creation.destroy_test_db(sqld_url, verbosity=0)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'core%' ORDER BY name"
        )
        rows = cursor.fetchall()
        for (table,) in rows:
            cursor.execute(f'DROP TABLE "{table}"')

    assert test_name == sqld_url
    assert rows == [
        ("core_attachment",),
        ("core_counter",),
        ("core_event",),
        ("core_product",),
        ("core_tag",),
    ]


# ---


def get_table_names(path: pathlib.Path) -> list[str]:
    handler = django.db.utils.ConnectionHandler(
        {"default": {"ENGINE": "dj_turso", "NAME": str(path)}}
    )
    connection = handler["default"]
    table_names: list[str] = connection.introspection.table_names()
    connection.close()
    return table_names


def get_tag_names(path: pathlib.Path) -> list[str]:
    connection = turso.connect(str(path), isolation_level=None)
    rows = connection.execute("SELECT name FROM core_tag ORDER BY name").fetchall()
    connection.close()
    return [name for (name,) in rows]
