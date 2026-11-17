import collections.abc
import pathlib
import typing as t

import django.db
import pytest
import pytest_django
import turso
from django.db.backends.base import base

from tests import utils
from tests.core import models

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

    assert table_names == ["core_attachment", "core_event", "core_tag"]


def test_clone_raises_not_supported_error(
    register_database: RegisterDatabase, tmp_path: pathlib.Path
) -> None:
    connection = register_database(
        "creation", {"ENGINE": "dj_turso", "NAME": str(tmp_path / "default.db")}
    )
    connection.creation.create_test_db(verbosity=0)

    with pytest.raises(django.db.NotSupportedError) as excinfo:
        connection.creation.clone_test_db(suffix="1", verbosity=0)
    connection.creation.destroy_test_db(str(tmp_path / "default.db"), verbosity=0)

    assert str(excinfo.value) == (
        "Turso test databases cannot be cloned: two processes cannot open one "
        "database file. Give each test process its own test database instead."
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
    assert rows == [("core_attachment",), ("core_event",), ("core_tag",)]
