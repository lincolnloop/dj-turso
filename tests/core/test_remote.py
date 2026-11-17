import errno
import importlib.metadata
import os
import pathlib
import shutil
import subprocess
import sys
import typing as t

import django.db
import django.db.utils
import pytest
import pytest_django

from dj_turso import base
from tests import utils


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("http://127.0.0.1:1/db/x", id="http"),
        pytest.param("https://example.invalid", id="https"),
        pytest.param("libsql://example.invalid", id="libsql"),
        pytest.param("turso://example.invalid", id="turso"),
    ],
)
def test_url_name_selects_serverless_driver(name: str) -> None:
    handler = django.db.utils.ConnectionHandler(
        {"default": {"ENGINE": "dj_turso", "NAME": name}}
    )

    connection = t.cast("base.DatabaseWrapper", handler["default"])

    assert connection.Database.__name__ == "turso_serverless"


# A real environment without the extra: the package and its required dependencies only.
def test_url_name_without_serverless_extra_raises_import_error() -> None:
    child = (
        "import django.conf, django.db.utils;"
        "django.conf.settings.configure();"
        "handler = django.db.utils.ConnectionHandler("
        "{'default': {'ENGINE': 'dj_turso', 'NAME': 'turso://example.invalid'}});"
        "handler['default']"
    )

    uv = shutil.which("uv")
    assert uv is not None

    completed = subprocess.run(
        [
            uv,
            "run",
            "--isolated",
            "--quiet",
            "--no-project",
            "--with-editable",
            ".",
            # Lets the run's coverage measure the child too; it is no driver.
            "--with",
            f"coverage=={importlib.metadata.version('coverage')}",
            "--python",
            f"{sys.version_info.major}.{sys.version_info.minor}",
            "python",
            "-I",
            "-W",
            "ignore::SyntaxWarning",
            "-c",
            child,
        ],
        capture_output=True,
        check=False,
        cwd=pathlib.Path(__file__).parents[2],
        # uv would pick this run's own environment, which has the extra.
        env={
            name: value for name, value in os.environ.items() if name != "VIRTUAL_ENV"
        },
        text=True,
    )

    assert completed.returncode == 1
    assert [line for line in completed.stderr.splitlines() if line[:1].isalpha()] == [
        "Traceback (most recent call last):",
        "ModuleNotFoundError: No module named 'turso_serverless'",
        "The above exception was the direct cause of the following exception:",
        "Traceback (most recent call last):",
        (
            "ImportError: A database NAME that is a URL needs the serverless extra: "
            "install dj-turso[serverless]."
        ),
    ]


def test_remote_query_returns_row(remote_target: base.DatabaseWrapper) -> None:
    with remote_target.cursor() as cursor:
        cursor.execute("SELECT 1, 'a'")
        rows = cursor.fetchall()

    assert rows == [(1, "a")]


def test_wrong_auth_token_raises_operational_error_with_cause(
    django_db_blocker: pytest_django.DjangoDbBlocker, sqld_url: str
) -> None:
    handler = django.db.utils.ConnectionHandler(
        {
            "default": {
                "ENGINE": "dj_turso",
                "NAME": sqld_url,
                "OPTIONS": {"auth_token": "wrong"},
            }
        }
    )
    connection = t.cast("base.DatabaseWrapper", handler["default"])

    with (
        django_db_blocker.unblock(),
        pytest.raises(django.db.OperationalError) as excinfo,
    ):
        connection.ensure_connection()

    assert str(excinfo.value) == "HTTP status 401: Unauthorized: `The JWT is invalid`"
    assert utils.get_cause_chain(excinfo.value) == [
        django.db.OperationalError,
        connection.Database.OperationalError,
    ]


def test_unreachable_host_raises_operational_error(
    django_db_blocker: pytest_django.DjangoDbBlocker,
) -> None:
    handler = django.db.utils.ConnectionHandler(
        {"default": {"ENGINE": "dj_turso", "NAME": "http://127.0.0.1:1"}}
    )
    connection = handler["default"]

    with (
        django_db_blocker.unblock(),
        pytest.raises(django.db.OperationalError) as excinfo,
    ):
        connection.ensure_connection()

    assert str(excinfo.value) == (
        "request to http://127.0.0.1:1/v3/cursor failed: "
        f"[Errno {errno.ECONNREFUSED}] {os.strerror(errno.ECONNREFUSED)}"
    )
