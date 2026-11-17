import collections.abc
import pathlib
import subprocess
import sys
import typing as t

import django.db
import django.db.utils
import pytest
import pytest_django

from dj_turso import base
from tests import utils


def test_unique_violation_raises_integrity_error_with_cause(
    target: base.DatabaseWrapper,
) -> None:
    with target.cursor() as cursor:
        cursor.execute("CREATE TABLE item (name TEXT UNIQUE)")
        cursor.execute("INSERT INTO item VALUES ('a')")
        with pytest.raises(django.db.IntegrityError) as excinfo:
            cursor.execute("INSERT INTO item VALUES ('a')")

    assert str(excinfo.value) == "UNIQUE constraint failed: item.name"
    assert utils.get_cause_chain(excinfo.value) == [
        django.db.IntegrityError,
        target.Database.IntegrityError,
    ]


@pytest.mark.parametrize(
    ("message", "sql"),
    [
        pytest.param(
            "Parse error: no such table: missing",
            "SELECT * FROM missing",
            id="parse-error",
        ),
        pytest.param('near "SELEC": syntax error', "SELEC 1", id="syntax-error"),
        pytest.param("incomplete input", "SELECT 1 FROM", id="incomplete-input"),
    ],
)
def test_invalid_sql_raises_operational_error_with_cause(
    message: str, sql: str, target: base.DatabaseWrapper
) -> None:
    with (
        target.cursor() as cursor,
        pytest.raises(django.db.OperationalError) as excinfo,
    ):
        cursor.execute(sql)

    assert str(excinfo.value) == message
    assert utils.get_cause_chain(excinfo.value) == [
        django.db.OperationalError,
        target.Database.OperationalError,
        target.Database.DatabaseError,
    ]


@pytest.mark.parametrize(
    "fetch",
    [
        pytest.param(lambda cursor: cursor.fetchall(), id="fetchall"),
        pytest.param(lambda cursor: cursor.fetchmany(2), id="fetchmany"),
        pytest.param(lambda cursor: cursor.fetchone(), id="fetchone"),
        pytest.param(list, id="iteration"),
    ],
)
def test_engine_error_while_fetching_raises_operational_error(
    fetch: collections.abc.Callable[[t.Any], object],
    target: base.DatabaseWrapper,
) -> None:
    with target.cursor() as cursor:
        cursor.execute("SELECT json('{')")
        with pytest.raises(django.db.OperationalError) as excinfo:
            fetch(cursor)

    assert str(excinfo.value) == "Parse error: malformed JSON"


def test_second_process_on_open_database_raises_operational_error(
    django_db_blocker: pytest_django.DjangoDbBlocker, tmp_path: pathlib.Path
) -> None:
    database_path = tmp_path / "default.db"
    handler = django.db.utils.ConnectionHandler(
        {"default": {"ENGINE": "dj_turso", "NAME": str(database_path)}}
    )
    connection = handler["default"]
    child = (
        "import django.conf, django.db.utils;"
        "django.conf.settings.configure();"
        "handler = django.db.utils.ConnectionHandler("
        f"{{'default': {{'ENGINE': 'dj_turso', 'NAME': {str(database_path)!r}}}}});"
        "handler['default'].ensure_connection()"
    )

    with django_db_blocker.unblock():
        connection.set_autocommit(False)
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE item (name TEXT)")
            cursor.execute("INSERT INTO item VALUES ('a')")
        completed = subprocess.run(
            [sys.executable, "-W", "ignore::SyntaxWarning", "-c", child],
            capture_output=True,
            check=False,
            text=True,
        )
        connection.rollback()
        connection.close()

    assert completed.returncode == 1
    assert completed.stderr.splitlines()[-1] == (
        "django.db.utils.OperationalError: Locking error: Failed locking file "
        f"'{database_path}'. File is locked by another process"
    )
