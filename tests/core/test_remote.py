import errno
import importlib.metadata
import os
import pathlib
import shutil
import subprocess
import sys
import time
import typing as t

import django.db
import django.db.utils
import pytest
import pytest_django
import pytest_httpserver
import turso_serverless
from turso_serverless import protocol
from turso_serverless import session as serverless_session

from dj_turso import base, exceptions, session
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
        "import sys, django.conf, django.db.utils;"
        "django.conf.settings.configure();"
        "handler = django.db.utils.ConnectionHandler("
        "{'default': {'ENGINE': 'dj_turso', 'NAME': 'turso://example.invalid'}});"
        "\ntry: handler['default']"
        "\nexcept ImportError as exc: sys.exit("
        "f'{type(exc).__name__}({exc.msg!r}) from {type(exc.__cause__).__name__}"
        "(msg={exc.__cause__.msg!r}, name={exc.__cause__.name!r})')"
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
    assert completed.stderr == (
        "ImportError('A database NAME that is a URL needs the serverless extra: "
        "install dj-turso[serverless].') from ModuleNotFoundError(msg=\"No module "
        "named 'turso_serverless'\", name='turso_serverless')\n"
    )


def test_remote_query_returns_row(remote_target: base.DatabaseWrapper) -> None:
    with remote_target.cursor() as cursor:
        cursor.execute("SELECT 1, 'a'")
        rows = cursor.fetchall()

    assert rows == [(1, "a")]


def test_wrong_auth_token_raises_operational_error_with_cause(
    django_db_blocker: pytest_django.DjangoDbBlocker, remote_settings: dict[str, t.Any]
) -> None:
    remote_settings["OPTIONS"] = {"auth_token": "wrong"}
    handler = django.db.utils.ConnectionHandler({"default": remote_settings})
    connection = t.cast("base.DatabaseWrapper", handler["default"])

    with (
        django_db_blocker.unblock(),
        pytest.raises(django.db.OperationalError) as excinfo,
    ):
        connection.ensure_connection()

    assert str(excinfo.value) == "HTTP status 400: JWT error: InvalidToken"
    assert utils.get_cause_chain(excinfo.value) == [
        django.db.OperationalError,
        connection.Database.OperationalError,
        protocol.ProtocolError,
    ]


def test_missing_auth_token_raises_operational_error(
    django_db_blocker: pytest_django.DjangoDbBlocker, remote_settings: dict[str, t.Any]
) -> None:
    del remote_settings["OPTIONS"]["auth_token"]
    handler = django.db.utils.ConnectionHandler({"default": remote_settings})
    connection = handler["default"]

    with (
        django_db_blocker.unblock(),
        pytest.raises(django.db.OperationalError) as excinfo,
    ):
        connection.ensure_connection()

    assert str(excinfo.value) == (
        "HTTP status 401: Unauthorized: `unauthorized access attempt on database: "
        "empty JWT token`"
    )


# The driver files an error coded by nothing as an integrity error when its message
# mentions a constraint, and a URL can.
def test_unreachable_url_naming_a_constraint_raises_operational_error(
    django_db_blocker: pytest_django.DjangoDbBlocker, loopback_network: None
) -> None:
    handler = django.db.utils.ConnectionHandler(
        {"default": {"ENGINE": "dj_turso", "NAME": "http://127.0.0.1:1/unique"}}
    )
    connection = handler["default"]

    with (
        django_db_blocker.unblock(),
        pytest.raises(django.db.OperationalError) as excinfo,
    ):
        connection.ensure_connection()

    assert str(excinfo.value) == (
        "request to /v3/pipeline failed: "
        f"[Errno {errno.ECONNREFUSED}] {os.strerror(errno.ECONNREFUSED)}"
    )


# As a proxy in front of the server might answer.
def test_error_status_without_json_body_raises_operational_error(
    django_db_blocker: pytest_django.DjangoDbBlocker,
    httpserver: pytest_httpserver.HTTPServer,
) -> None:
    httpserver.expect_oneshot_request("/v3/pipeline").respond_with_data(
        "<html>Not Found</html>", status=404, content_type="text/html"
    )
    handler = django.db.utils.ConnectionHandler(
        {
            "default": {
                "ENGINE": "dj_turso",
                "NAME": httpserver.url_for("").rstrip("/"),
            }
        }
    )
    connection = handler["default"]

    with (
        django_db_blocker.unblock(),
        pytest.raises(django.db.OperationalError) as excinfo,
    ):
        connection.ensure_connection()

    assert str(excinfo.value) == "HTTP status 404"


# libSQL, as sqld and Turso Cloud's databases of type SQLite run, has no
# turso_version(). Django checks the database once per alias per process: the test's
# own alias.
@pytest.mark.parametrize(
    "message",
    [
        pytest.param(
            "SQL input error: no such function: turso_version (at offset 7)",
            id="sqld",
        ),
        pytest.param("SQLite error: no such function: turso_version", id="cloud"),
    ],
)
def test_engine_without_turso_version_raises_not_supported_error(
    django_db_blocker: pytest_django.DjangoDbBlocker,
    httpserver: pytest_httpserver.HTTPServer,
    message: str,
    request: pytest.FixtureRequest,
) -> None:
    utils.expect_stream_start(httpserver)
    httpserver.expect_oneshot_request("/v3/cursor").respond_with_data(
        '{"baton": "baton", "base_url": null}\n'
        '{"type": "step_error", "step": 0, "error": {"message": "' + message + '"}}\n'
    )
    handler = django.db.utils.ConnectionHandler(
        {
            "default": {
                "ENGINE": "dj_turso",
                "NAME": httpserver.url_for("").rstrip("/"),
            }
        }
    )
    connection = base.DatabaseWrapper(handler.settings["default"], request.node.name)

    with (
        django_db_blocker.unblock(),
        pytest.raises(django.db.NotSupportedError) as excinfo,
    ):
        connection.ensure_connection()
    connection.close()

    assert str(excinfo.value) == (
        "dj-turso requires the Turso engine: the database has no turso_version() "
        "function."
    )
    assert utils.get_cause_chain(excinfo.value) == [
        exceptions.EngineNotSupportedError,
        django.db.OperationalError,
        turso_serverless.OperationalError,
    ]


def test_other_error_from_turso_version_raises_it(
    django_db_blocker: pytest_django.DjangoDbBlocker,
    httpserver: pytest_httpserver.HTTPServer,
    request: pytest.FixtureRequest,
) -> None:
    utils.expect_stream_start(httpserver)
    httpserver.expect_oneshot_request("/v3/cursor").respond_with_data(
        '{"baton": "baton", "base_url": null}\n'
        '{"type": "step_error", "step": 0, "error": {"message": "Tursodb error: '
        'Database is busy"}}\n'
    )
    handler = django.db.utils.ConnectionHandler(
        {
            "default": {
                "ENGINE": "dj_turso",
                "NAME": httpserver.url_for("").rstrip("/"),
            }
        }
    )
    connection = base.DatabaseWrapper(handler.settings["default"], request.node.name)

    with (
        django_db_blocker.unblock(),
        pytest.raises(django.db.OperationalError) as excinfo,
    ):
        connection.ensure_connection()
    connection.close()

    assert str(excinfo.value) == "Tursodb error: Database is busy"


# The driver decodes a malformed row without catching its TypeError.
def test_type_error_decoding_response_is_not_relabeled(
    django_db_blocker: pytest_django.DjangoDbBlocker,
    httpserver: pytest_httpserver.HTTPServer,
    request: pytest.FixtureRequest,
) -> None:
    utils.expect_stream_start(httpserver)
    httpserver.expect_oneshot_request("/v3/cursor").respond_with_data(
        '{"baton": "baton", "base_url": null}\n'
        '{"type": "step_begin", "step": 0, "cols": [{"name": "turso_version()"}]}\n'
        '{"type": "row", "row": 5}\n'
    )
    handler = django.db.utils.ConnectionHandler(
        {"default": {"ENGINE": "dj_turso", "NAME": httpserver.url_for("").rstrip("/")}}
    )
    connection = base.DatabaseWrapper(handler.settings["default"], request.node.name)

    with django_db_blocker.unblock(), pytest.raises(TypeError) as excinfo:
        connection.ensure_connection()
    connection.close()

    assert type(excinfo.value) is TypeError
    assert str(excinfo.value) == "'int' object is not iterable"


# Turso Cloud runs a newer engine than the stub's pyturso, without the prefix.
@pytest.mark.parametrize(
    ("message", "remote_settings"),
    [
        pytest.param("Tursodb error: no such table: missing", "cloud", id="cloud"),
        pytest.param(
            "Tursodb error: Parse error: no such table: missing", "stub", id="stub"
        ),
    ],
    indirect=["remote_settings"],
)
def test_failing_init_command_raises_operational_error(
    django_db_blocker: pytest_django.DjangoDbBlocker,
    message: str,
    remote_settings: dict[str, t.Any],
) -> None:
    remote_settings["OPTIONS"]["init_command"] = "SELECT * FROM missing"
    handler = django.db.utils.ConnectionHandler({"default": remote_settings})
    connection = handler["default"]

    with (
        django_db_blocker.unblock(),
        pytest.raises(django.db.OperationalError) as excinfo,
    ):
        connection.ensure_connection()

    assert str(excinfo.value) == message


# How the server expires an idle stream: measured on Turso Cloud, and so answered by
# the stub. Upstream's session, unlike dj-turso's, replaces no idle stream.
@pytest.mark.timeout(40)
def test_idle_transaction_after_pipeline_is_rolled_back(
    remote_settings: dict[str, t.Any],
) -> None:
    remote_session = serverless_session.Session(
        remote_settings["NAME"], auth_token=remote_settings["OPTIONS"]["auth_token"]
    )
    remote_session.execute_pipeline([{"type": "execute", "stmt": {"sql": "BEGIN"}}])
    time.sleep(session.STREAM_IDLE_TIMEOUT + 2)

    results = remote_session.execute_pipeline(
        [{"type": "execute", "stmt": {"sql": "SELECT 1"}}]
    )

    assert results == [
        {
            "type": "error",
            "error": {
                "message": "SQLite error: interactive transaction was rolled back "
                "because the stream was idle for too long; retry the transaction",
                "code": "SQLITE_BUSY",
            },
        }
    ]
    assert remote_session.autocommit is True
    assert remote_session._baton is None


@pytest.mark.timeout(40)
def test_idle_transaction_after_pipeline_is_forgotten_later(
    remote_settings: dict[str, t.Any],
) -> None:
    remote_session = serverless_session.Session(
        remote_settings["NAME"], auth_token=remote_settings["OPTIONS"]["auth_token"]
    )
    remote_session.execute_pipeline([{"type": "execute", "stmt": {"sql": "BEGIN"}}])
    stream_id = str(remote_session._baton).rpartition(":")[0]
    time.sleep(2 * session.STREAM_IDLE_TIMEOUT + 1)

    with pytest.raises(protocol.ProtocolError) as excinfo:
        remote_session.execute_pipeline(
            [{"type": "execute", "stmt": {"sql": "SELECT 1"}}]
        )

    assert str(excinfo.value) == f"HTTP status 404: stream not found: {stream_id}"


@pytest.mark.timeout(40)
def test_idle_session_state_after_pipeline_is_forgotten(
    remote_settings: dict[str, t.Any],
) -> None:
    remote_session = serverless_session.Session(
        remote_settings["NAME"], auth_token=remote_settings["OPTIONS"]["auth_token"]
    )
    remote_session.execute_pipeline(
        [{"type": "execute", "stmt": {"sql": "PRAGMA foreign_keys = OFF"}}]
    )
    stream_id = str(remote_session._baton).rpartition(":")[0]
    time.sleep(session.STREAM_IDLE_TIMEOUT + 2)

    with pytest.raises(protocol.ProtocolError) as excinfo:
        remote_session.execute_pipeline(
            [{"type": "execute", "stmt": {"sql": "SELECT 1"}}]
        )

    assert str(excinfo.value) == f"HTTP status 404: stream not found: {stream_id}"


@pytest.mark.timeout(50)
def test_idle_transaction_after_cursor_is_rolled_back_later(
    remote_settings: dict[str, t.Any],
) -> None:
    remote_session = serverless_session.Session(
        remote_settings["NAME"], auth_token=remote_settings["OPTIONS"]["auth_token"]
    )
    remote_session.execute_stmt("BEGIN")
    time.sleep(3 * session.STREAM_IDLE_TIMEOUT + 1)

    with pytest.raises(protocol.ServerError) as excinfo:
        remote_session.execute_stmt("SELECT 1")
    autocommit = remote_session.autocommit
    remote_session.close()

    assert str(excinfo.value) == (
        "SQLite error: interactive transaction was rolled back because the stream "
        "was idle for too long; retry the transaction"
    )
    assert excinfo.value.code == "SQLITE_BUSY"
    assert autocommit is True


@pytest.mark.timeout(40)
def test_idle_transaction_after_cursor_survives(
    remote_settings: dict[str, t.Any],
) -> None:
    remote_session = serverless_session.Session(
        remote_settings["NAME"], auth_token=remote_settings["OPTIONS"]["auth_token"]
    )
    remote_session.execute_stmt("BEGIN")
    time.sleep(2 * session.STREAM_IDLE_TIMEOUT + 1)

    result = remote_session.execute_stmt("SELECT 1")
    autocommit = remote_session.autocommit
    remote_session.close()

    assert result.rows == [(1,)]
    assert autocommit is False
