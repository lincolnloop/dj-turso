import concurrent.futures
import importlib.metadata
import pathlib

import django.db
import django.db.utils
import pytest
import pytest_django

from dj_turso import base
from tests.core import models


def test_engine_identifies_as_turso_on_sqlite_vendor(
    target: base.DatabaseWrapper,
) -> None:
    assert target.vendor == "sqlite"
    assert target.display_name == "Turso"


def test_queries_run_on_turso_engine(target: base.DatabaseWrapper) -> None:
    with target.cursor() as cursor:
        cursor.execute("SELECT turso_version()")
        rows = cursor.fetchall()

    assert rows == [(importlib.metadata.version("pyturso"),)]


def test_select_returns_row(target: base.DatabaseWrapper) -> None:
    with target.cursor() as cursor:
        cursor.execute("SELECT 1, 'a'")
        rows = cursor.fetchall()

    assert rows == [(1, "a")]


def test_foreign_keys_enforced_on_new_connection(
    target: base.DatabaseWrapper,
) -> None:
    with target.cursor() as cursor:
        cursor.execute("PRAGMA foreign_keys")
        rows = cursor.fetchall()

    assert rows == [(1,)]


def test_path_object_name_opens_local_file(
    django_db_blocker: pytest_django.DjangoDbBlocker, tmp_path: pathlib.Path
) -> None:
    database_path = tmp_path / "default.db"
    handler = django.db.utils.ConnectionHandler(
        {"default": {"ENGINE": "dj_turso", "NAME": database_path}}
    )
    connection = handler["default"]

    with django_db_blocker.unblock():
        connection.ensure_connection()
        connection.close()

    assert database_path.exists() is True


def test_options_pass_through_to_driver(
    django_db_blocker: pytest_django.DjangoDbBlocker, tmp_path: pathlib.Path
) -> None:
    handler = django.db.utils.ConnectionHandler(
        {
            "default": {
                "ENGINE": "dj_turso",
                "NAME": str(tmp_path / "default.db"),
                "OPTIONS": {"experimental_features": "generated_columns"},
            }
        }
    )
    connection = handler["default"]

    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute("CREATE TABLE item (a INTEGER, b INTEGER AS (a + 1))")
        cursor.execute("INSERT INTO item (a) VALUES (1)")
        cursor.execute("SELECT a, b FROM item")
        rows = cursor.fetchall()
    connection.close()

    assert rows == [(1, 2)]


def test_auth_token_with_local_name_raises_type_error(
    django_db_blocker: pytest_django.DjangoDbBlocker, tmp_path: pathlib.Path
) -> None:
    handler = django.db.utils.ConnectionHandler(
        {
            "default": {
                "ENGINE": "dj_turso",
                "NAME": str(tmp_path / "default.db"),
                "OPTIONS": {"auth_token": "remote-only"},
            }
        }
    )
    connection = handler["default"]

    with django_db_blocker.unblock(), pytest.raises(TypeError) as excinfo:
        connection.ensure_connection()

    assert (
        str(excinfo.value)
        == "connect() got an unexpected keyword argument 'auth_token'"
    )


def test_init_command_runs_after_connection_setup(
    django_db_blocker: pytest_django.DjangoDbBlocker, tmp_path: pathlib.Path
) -> None:
    handler = django.db.utils.ConnectionHandler(
        {
            "default": {
                "ENGINE": "dj_turso",
                "NAME": str(tmp_path / "default.db"),
                "OPTIONS": {"init_command": "PRAGMA foreign_keys = OFF;"},
            }
        }
    )
    connection = handler["default"]

    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute("PRAGMA foreign_keys")
        rows = cursor.fetchall()
    connection.close()

    assert rows == [(0,)]


# Django's sqlite3 backend waits on a locked database as long as stdlib sqlite3's
# connect() timeout, 5 seconds unless OPTIONS says otherwise.
@pytest.mark.parametrize(
    ("busy_timeout", "options"),
    [
        pytest.param(5000, {}, id="default"),
        pytest.param(250, {"timeout": 0.25}, id="option"),
    ],
)
def test_local_connection_waits_on_lock_for_timeout(
    busy_timeout: int,
    django_db_blocker: pytest_django.DjangoDbBlocker,
    options: dict[str, float],
    tmp_path: pathlib.Path,
) -> None:
    handler = django.db.utils.ConnectionHandler(
        {
            "default": {
                "ENGINE": "dj_turso",
                "NAME": str(tmp_path / "default.db"),
                "OPTIONS": options,
            }
        }
    )
    connection = handler["default"]

    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute("PRAGMA busy_timeout")
        rows = cursor.fetchall()
    connection.close()

    assert rows == [(busy_timeout,)]


# Django checks the database once per alias per process: the test's own alias.
def test_turso_engine_passes_database_check(
    driver_target: base.DatabaseWrapper, request: pytest.FixtureRequest
) -> None:
    connection = base.DatabaseWrapper(driver_target.settings_dict, request.node.name)

    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()
    connection.close()

    assert rows == [(1,)]


@pytest.mark.django_db(transaction=True)
def test_threads_share_database_file() -> None:
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(create_tag_in_thread, name) for name in ["first", "second"]
        ]
        for future in futures:
            future.result()

    names = models.Tag.objects.order_by("name").values_list("name", flat=True)

    assert list(names) == ["first", "second"]


# ---


def create_tag_in_thread(name: str) -> None:
    models.Tag.objects.create(name=name)
    django.db.connection.close()
