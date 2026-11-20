import collections.abc
import http
import http.client
import json
import os
import resource
import socket
import time
import typing as t
import urllib.parse

import django.db
import django.db.utils
import pytest
import pytest_django
import pytest_httpserver
from django.db import transaction
from turso_serverless import protocol

from dj_turso import base, session
from tests import utils


# Fills the descriptor table so the next socket opened lands at 1024 or above, past
# what select() can watch.
@pytest.fixture
def high_file_descriptors() -> collections.abc.Iterator[None]:
    soft_limit, hard_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (max(soft_limit, 1100), hard_limit))
    descriptors = [os.open(os.devnull, os.O_RDONLY)]
    while descriptors[-1] < 1023:
        descriptors.append(os.dup(descriptors[0]))
    yield
    for descriptor in descriptors:
        os.close(descriptor)
    resource.setrlimit(resource.RLIMIT_NOFILE, (soft_limit, hard_limit))


# The proxied target under an alias of Django's own handler, for transaction.atomic.
@pytest.fixture
def proxied_alias(
    proxied_target: base.DatabaseWrapper,
) -> collections.abc.Iterator[str]:
    django.db.connections["proxied"] = proxied_target
    yield "proxied"
    del django.db.connections["proxied"]


# Each driver's target under its alias in Django's own handler, for transaction.atomic.
@pytest.fixture
def driver_alias(
    driver_target: base.DatabaseWrapper,
) -> collections.abc.Iterator[str]:
    django.db.connections[driver_target.alias] = driver_target
    yield driver_target.alias
    del django.db.connections[driver_target.alias]


def test_queries_reuse_one_tcp_connection(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    with proxied_target.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.execute("SELECT 2")
        cursor.execute("SELECT 3")

    assert proxy.accepted_connection_count == 1


def test_idle_closed_socket_reconnects_before_send(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    with proxied_target.cursor() as cursor:
        cursor.execute("SELECT 1")
        proxy.drop_connections()
        cursor.execute("SELECT 2")
        rows = cursor.fetchall()

    assert rows == [(2,)]
    assert proxy.accepted_connection_count == 2
    # The connection's foreign key pragma, then each query once.
    assert proxy.received_request_count == 3


@pytest.mark.usefixtures("high_file_descriptors")
def test_socket_past_select_limit_is_reused(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    with proxied_target.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.execute("SELECT 2")
        rows = cursor.fetchall()

    assert rows == [(2,)]
    assert proxy.accepted_connection_count == 1


def test_close_closes_socket(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    proxied_target.ensure_connection()

    proxied_target.close()

    assert proxy.wait_for_open_connection_count(0) == 0


def test_failed_session_setup_closes_socket(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    proxied_target.settings_dict["OPTIONS"]["init_command"] = "SELECT * FROM missing"

    with pytest.raises(django.db.OperationalError) as excinfo:
        proxied_target.ensure_connection()

    assert str(excinfo.value) == "Tursodb error: Parse error: no such table: missing"
    assert proxy.wait_for_open_connection_count(0) == 0


def test_failed_request_outside_transaction_is_not_resent(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    proxied_target.ensure_connection()
    proxy.fail_next_request()

    with (
        proxied_target.cursor() as cursor,
        pytest.raises(django.db.OperationalError) as excinfo,
    ):
        cursor.execute("SELECT 1")

    assert str(excinfo.value) == (
        "request to /v3/cursor failed: Remote end closed connection without response"
    )
    assert proxy.received_request_count == 2
    assert proxied_target.is_usable() is True


def test_failed_request_inside_transaction_raises_and_poisons_connection(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    with proxied_target.cursor() as cursor:
        cursor.execute("BEGIN")
        cursor.execute("SELECT 0")
        proxy.fail_next_request()
        with pytest.raises(django.db.OperationalError) as excinfo:
            cursor.execute("SELECT 1")

    assert str(excinfo.value) == (
        "Lost the connection to the remote database inside a transaction: whether "
        "it committed is unknown, and this connection cannot be used again."
    )
    assert utils.get_cause_chain(excinfo.value) == [
        django.db.OperationalError,
        session.LostTransactionError,
        protocol.ProtocolError,
        http.client.RemoteDisconnected,
    ]
    assert proxy.received_request_count == 3
    assert proxied_target.is_usable() is False


def test_lost_transaction_connection_is_dropped_when_django_checks_it(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    with proxied_target.cursor() as cursor:
        cursor.execute("BEGIN")
        proxy.fail_next_request()
        with pytest.raises(django.db.OperationalError):
            cursor.execute("SELECT 1")

    proxied_target.close_if_unusable_or_obsolete()
    with proxied_target.cursor() as cursor:
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()

    assert rows == [(1,)]


def test_lost_transaction_in_atomic_block_closes_connection(
    proxied_alias: str, proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    @transaction.atomic(using=proxied_alias)
    def lose_transaction() -> None:
        with proxied_target.cursor() as cursor:
            cursor.execute("SELECT 0")
            proxy.fail_next_request()
            cursor.execute("SELECT 1")

    with pytest.raises(django.db.OperationalError) as excinfo:
        lose_transaction()
    with proxied_target.cursor() as cursor:
        cursor.execute("SELECT 2")
        rows = cursor.fetchall()

    assert str(excinfo.value) == (
        "Lost the connection to the remote database inside a transaction: whether "
        "it committed is unknown, and this connection cannot be used again."
    )
    assert rows == [(2,)]


def test_lost_transaction_caught_in_atomic_block_fails_commit_and_closes_connection(
    proxied_alias: str, proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    @transaction.atomic(using=proxied_alias)
    def lose_transaction_quietly() -> None:
        with proxied_target.cursor() as cursor:
            cursor.execute("SELECT 0")
            proxy.fail_next_request()
            with pytest.raises(django.db.OperationalError):
                cursor.execute("SELECT 1")

    with pytest.raises(django.db.OperationalError) as excinfo:
        lose_transaction_quietly()
    with proxied_target.cursor() as cursor:
        cursor.execute("SELECT 2")
        rows = cursor.fetchall()

    assert str(excinfo.value) == (
        "Lost the connection to the remote database inside a transaction: whether "
        "it committed is unknown, and this connection cannot be used again."
    )
    assert rows == [(2,)]


def test_lost_transaction_fails_every_later_statement_unsent(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    with proxied_target.cursor() as cursor:
        cursor.execute("BEGIN")
        cursor.execute("SELECT 0")
        proxy.fail_next_request()
        with pytest.raises(django.db.OperationalError):
            cursor.execute("SELECT 1")
        with pytest.raises(django.db.OperationalError) as excinfo:
            cursor.execute("SELECT 2")

    assert str(excinfo.value) == (
        "Lost the connection to the remote database inside a transaction: whether "
        "it committed is unknown, and this connection cannot be used again."
    )
    assert utils.get_cause_chain(excinfo.value) == [
        django.db.OperationalError,
        session.LostTransactionError,
    ]
    assert proxy.received_request_count == 3


def test_malformed_response_inside_transaction_loses_transaction(
    httpserver: pytest_httpserver.HTTPServer, stub_target: base.DatabaseWrapper
) -> None:
    with stub_target.cursor() as cursor:
        cursor.execute("BEGIN")
        cursor.execute("SELECT 0")
        httpserver.expect_oneshot_request("/v3/cursor").respond_with_data("not json\n")
        with pytest.raises(django.db.OperationalError) as excinfo:
            cursor.execute("SELECT 1")

    assert str(excinfo.value) == (
        "Lost the connection to the remote database inside a transaction: whether "
        "it committed is unknown, and this connection cannot be used again."
    )
    assert utils.get_cause_chain(excinfo.value) == [
        django.db.OperationalError,
        session.LostTransactionError,
        protocol.ProtocolError,
        json.JSONDecodeError,
    ]
    assert stub_target.is_usable() is False


# A cursor error makes the driver ask for the transaction state in a pipeline, whose
# malformed answer ends the stream.
def test_malformed_state_answer_inside_transaction_loses_transaction(
    httpserver: pytest_httpserver.HTTPServer, stub_target: base.DatabaseWrapper
) -> None:
    with stub_target.cursor() as cursor:
        cursor.execute("BEGIN")
        cursor.execute("SELECT 0")
        httpserver.expect_oneshot_request("/v3/cursor").respond_with_data(
            '{"baton": "baton", "base_url": null}\n'
            '{"type": "error", "error": {"message": "cursor failed"}}\n'
        )
        httpserver.expect_oneshot_request("/v3/pipeline").respond_with_data("not json")
        with pytest.raises(django.db.OperationalError) as excinfo:
            cursor.execute("SELECT 1")

    assert str(excinfo.value) == (
        "Lost the connection to the remote database inside a transaction: whether "
        "it committed is unknown, and this connection cannot be used again."
    )
    assert utils.get_cause_chain(excinfo.value) == [
        django.db.OperationalError,
        session.LostTransactionError,
        protocol.ServerError,
    ]
    assert stub_target.is_usable() is False


# When the transaction state is unclear after a statement, the driver asks for it in
# a pipeline, and on a malformed answer ends the stream yet returns the statement.
def test_stream_ended_after_successful_statement_loses_transaction(
    httpserver: pytest_httpserver.HTTPServer, stub_target: base.DatabaseWrapper
) -> None:
    with stub_target.cursor() as cursor:
        cursor.execute("BEGIN")
        cursor.execute("SELECT 0")
        httpserver.expect_oneshot_request("/v3/cursor").respond_with_data(
            '{"baton": "baton", "base_url": null}\n'
            '{"type": "step_begin", "step": 0, "cols": [{"name": "1"}]}\n'
            '{"type": "row", "row": [{"type": "integer", "value": "1"}]}\n'
            '{"type": "step_end", "affected_row_count": 0, "last_insert_rowid": null}\n'
            '{"type": "step_error", "step": 1, "error": {"message": "unclear"}}\n'
        )
        httpserver.expect_oneshot_request("/v3/pipeline").respond_with_data("not json")
        with pytest.raises(django.db.OperationalError) as excinfo:
            cursor.execute("SELECT 1")

    assert str(excinfo.value) == (
        "Lost the connection to the remote database inside a transaction: whether "
        "it committed is unknown, and this connection cannot be used again."
    )
    assert utils.get_cause_chain(excinfo.value) == [
        django.db.OperationalError,
        session.LostTransactionError,
    ]
    assert stub_target.is_usable() is False


def test_new_stream_repeats_session_setup(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    proxied_target.settings_dict["OPTIONS"]["init_command"] = "PRAGMA cache_size = 1234"
    proxied_target.ensure_connection()
    proxy.fail_next_request()

    with proxied_target.cursor() as cursor:
        with pytest.raises(django.db.OperationalError):
            cursor.execute("SELECT 1")
        cursor.execute("PRAGMA foreign_keys")
        foreign_keys = cursor.fetchall()
        cursor.execute("PRAGMA cache_size")
        cache_size = cursor.fetchall()

    assert foreign_keys == [(1,)]
    assert cache_size == [(1234,)]
    # Setup, the failed query, setup again, then each query once.
    assert proxy.received_request_count == 5


def test_failed_setup_of_new_stream_is_not_used_by_next_query(
    httpserver: pytest_httpserver.HTTPServer,
    proxied_target: base.DatabaseWrapper,
    proxy: utils.CountingProxy,
) -> None:
    proxied_target.ensure_connection()
    proxy.fail_next_request()

    with proxied_target.cursor() as cursor:
        with pytest.raises(django.db.OperationalError):
            cursor.execute("SELECT 1")
        httpserver.expect_oneshot_request("/v3/pipeline").respond_with_json(
            {
                "baton": "half-set-up",
                "base_url": None,
                "results": [
                    {"type": "error", "error": {"message": "setup failed"}},
                    {
                        "type": "ok",
                        "response": {"type": "get_autocommit", "is_autocommit": True},
                    },
                ],
            }
        )
        with pytest.raises(django.db.OperationalError) as excinfo:
            cursor.execute("SELECT 2")
        cursor.execute("SELECT 3")
        rows = cursor.fetchall()

    assert str(excinfo.value) == "setup failed"
    assert rows == [(3,)]


def test_connection_closed_by_server_answer_reconnects(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    proxy.close_after_next_answer()
    proxied_target.ensure_connection()

    with proxied_target.cursor() as cursor:
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()

    assert rows == [(1,)]
    assert proxy.accepted_connection_count == 2


# Past where the server expires a stream and rolls back its transaction.
SECONDS_PAST_STREAM_EXPIRY = 2.5


@pytest.mark.timeout(30)
def test_query_after_stream_expiry_starts_new_stream_unsent_to_old(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    with proxied_target.cursor() as cursor:
        cursor.execute("SELECT 1")
        time.sleep(session.STREAM_IDLE_TIMEOUT + SECONDS_PAST_STREAM_EXPIRY)
        cursor.execute("SELECT 2")
        rows = cursor.fetchall()

    assert rows == [(2,)]
    # Setup and a query on each stream, and the old one's close between them.
    assert proxy.received_request_count == 5


# The server's idle clock starts when an answer leaves it, a second before the
# delayed answer reaches the client.
@pytest.mark.timeout(30)
def test_query_after_stream_expiry_on_slow_link_starts_new_stream(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    with proxied_target.cursor() as cursor:
        proxy.delay_next_response(1)
        cursor.execute("SELECT 1")
        time.sleep(session.STREAM_IDLE_TIMEOUT - 0.4)
        cursor.execute("SELECT 2")
        rows = cursor.fetchall()

    assert rows == [(2,)]


@pytest.mark.timeout(30)
def test_query_after_stream_expiry_runs_with_session_setup(
    remote_target: base.DatabaseWrapper,
) -> None:
    with remote_target.cursor() as cursor:
        cursor.execute("SELECT 1")
        time.sleep(session.STREAM_IDLE_TIMEOUT + SECONDS_PAST_STREAM_EXPIRY)
        cursor.execute("PRAGMA foreign_keys")
        rows = cursor.fetchall()

    assert rows == [(1,)]


@pytest.mark.timeout(30)
def test_disabled_constraint_checking_survives_stream_expiry(
    remote_target: base.DatabaseWrapper,
) -> None:
    disabled = remote_target.disable_constraint_checking()
    time.sleep(session.STREAM_IDLE_TIMEOUT + SECONDS_PAST_STREAM_EXPIRY)
    with remote_target.cursor() as cursor:
        cursor.execute("PRAGMA foreign_keys")
        rows = cursor.fetchall()

    assert disabled is True
    assert rows == [(0,)]


@pytest.mark.timeout(30)
def test_reenabled_constraint_checking_survives_stream_expiry(
    remote_target: base.DatabaseWrapper,
) -> None:
    remote_target.disable_constraint_checking()
    remote_target.enable_constraint_checking()
    time.sleep(session.STREAM_IDLE_TIMEOUT + SECONDS_PAST_STREAM_EXPIRY)
    with remote_target.cursor() as cursor:
        cursor.execute("PRAGMA foreign_keys")
        rows = cursor.fetchall()

    assert rows == [(1,)]


# SQLite ignores the pragma inside a transaction, and Turso applies it: either way,
# a new stream keeps what the old one had.
@pytest.mark.timeout(30)
def test_constraint_checking_disabled_inside_transaction_survives_stream_expiry(
    remote_target: base.DatabaseWrapper,
) -> None:
    with remote_target.cursor() as cursor:
        cursor.execute("BEGIN")
        remote_target.disable_constraint_checking()
        cursor.execute("COMMIT")
        cursor.execute("PRAGMA foreign_keys")
        rows_before_expiry = cursor.fetchall()
        time.sleep(session.STREAM_IDLE_TIMEOUT + SECONDS_PAST_STREAM_EXPIRY)
        cursor.execute("PRAGMA foreign_keys")
        rows = cursor.fetchall()

    assert rows == rows_before_expiry


@pytest.mark.timeout(30)
def test_close_after_stream_expiry_starts_no_new_stream(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    with proxied_target.cursor() as cursor:
        cursor.execute("SELECT 1")
    time.sleep(session.STREAM_IDLE_TIMEOUT + SECONDS_PAST_STREAM_EXPIRY)

    proxied_target.close()

    # Setup, the query, then the close on the old stream.
    assert proxy.received_request_count == 3


# Turso Cloud keeps a transaction idle past the stream limit for a while, and
# answers when it has rolled it back.
@pytest.mark.timeout(30)
def test_idle_transaction_is_sent_to_its_stream(
    proxied_alias: str, proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    with proxied_target.cursor() as cursor:
        cursor.execute("CREATE TABLE item (name TEXT)")

    with transaction.atomic(using=proxied_alias), proxied_target.cursor() as cursor:
        cursor.execute("INSERT INTO item VALUES ('before')")
        time.sleep(session.STREAM_IDLE_TIMEOUT + SECONDS_PAST_STREAM_EXPIRY)
        cursor.execute("INSERT INTO item VALUES ('after')")
    with proxied_target.cursor() as cursor:
        cursor.execute("SELECT name FROM item ORDER BY name")
        rows = cursor.fetchall()

    assert rows == [("after",), ("before",)]
    # Setup, CREATE, BEGIN with the first INSERT, the second, and COMMIT, all on one
    # stream, then SELECT.
    assert proxy.received_request_count == 6


@pytest.mark.timeout(30)
def test_transaction_rolled_back_for_idling_is_lost(
    proxied_target: base.DatabaseWrapper,
) -> None:
    with proxied_target.cursor() as cursor:
        cursor.execute("BEGIN")
        cursor.execute("SELECT 0")
        time.sleep(3 * session.STREAM_IDLE_TIMEOUT)
        with pytest.raises(django.db.OperationalError) as excinfo:
            cursor.execute("SELECT 1")
        with pytest.raises(django.db.OperationalError) as commit_excinfo:
            proxied_target.commit()

    assert str(excinfo.value) == (
        "The remote database rolled back the transaction for idling: it did not "
        "commit, and this connection cannot be used again."
    )
    assert utils.get_cause_chain(excinfo.value) == [
        django.db.OperationalError,
        session.LostTransactionError,
        protocol.ServerError,
    ]
    assert str(commit_excinfo.value) == (
        "The remote database rolled back the transaction for idling: it did not "
        "commit, and this connection cannot be used again."
    )
    assert utils.get_cause_chain(commit_excinfo.value) == [
        django.db.OperationalError,
        session.LostTransactionError,
    ]
    assert proxied_target.is_usable() is False


def test_stream_not_found_inside_transaction_loses_transaction(
    httpserver: pytest_httpserver.HTTPServer, stub_target: base.DatabaseWrapper
) -> None:
    with stub_target.cursor() as cursor:
        cursor.execute("BEGIN")
        cursor.execute("SELECT 0")
        httpserver.expect_oneshot_request("/v3/cursor").respond_with_json(
            {"error": "stream not found: 0:1"}, status=http.HTTPStatus.NOT_FOUND
        )
        with pytest.raises(django.db.OperationalError) as excinfo:
            cursor.execute("SELECT 1")
        with pytest.raises(django.db.OperationalError) as rollback_excinfo:
            stub_target.rollback()

    assert str(excinfo.value) == (
        "Lost the connection to the remote database inside a transaction: whether "
        "it committed is unknown, and this connection cannot be used again."
    )
    assert utils.get_cause_chain(excinfo.value) == [
        django.db.OperationalError,
        session.LostTransactionError,
        protocol.ProtocolError,
    ]
    assert utils.get_cause_chain(rollback_excinfo.value) == [
        django.db.OperationalError,
        session.LostTransactionError,
    ]
    assert stub_target.is_usable() is False


# Turso Cloud names the error "error"; as upstream, "message" counts too, and only a
# string.
@pytest.mark.parametrize(
    ("body", "message"),
    [
        pytest.param(
            b'{"error": "bad baton", "message": "stream expired"}',
            "HTTP status 400: bad baton",
            id="error",
        ),
        pytest.param(
            b'{"error": 5, "message": "stream expired"}',
            "HTTP status 400: stream expired",
            id="message",
        ),
        pytest.param(b'{"error": 5}', "HTTP status 400", id="no-string"),
        pytest.param(b'["stream expired"]', "HTTP status 400", id="not-object"),
    ],
)
def test_error_status_raises_operational_error_with_server_message(
    body: bytes,
    httpserver: pytest_httpserver.HTTPServer,
    message: str,
    stub_target: base.DatabaseWrapper,
) -> None:
    stub_target.ensure_connection()
    httpserver.expect_oneshot_request("/v3/cursor").respond_with_data(
        body, status=http.HTTPStatus.BAD_REQUEST, content_type="application/json"
    )

    with (
        stub_target.cursor() as cursor,
        pytest.raises(django.db.OperationalError) as excinfo,
    ):
        cursor.execute("SELECT 1")

    assert str(excinfo.value) == message


# Statements may run as long as they need: only connecting is timed.
@pytest.mark.timeout(session.CONNECT_TIMEOUT + 10)
def test_answer_slower_than_connect_timeout_is_awaited(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    proxied_target.ensure_connection()
    proxy.delay_next_response(session.CONNECT_TIMEOUT + 1)

    with proxied_target.cursor() as cursor:
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()

    assert rows == [(1,)]
    assert proxy.received_request_count == 2


@pytest.mark.timeout(session.CONNECT_TIMEOUT + 10)
def test_answer_slower_than_connect_timeout_is_awaited_after_reconnect(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    proxy.close_after_next_answer()
    proxied_target.ensure_connection()

    with proxied_target.cursor() as cursor:
        cursor.execute("SELECT 1")
        proxy.delay_next_response(session.CONNECT_TIMEOUT + 1)
        cursor.execute("SELECT 2")
        rows = cursor.fetchall()

    assert rows == [(2,)]
    assert proxy.accepted_connection_count == 2


# A server that accepts the TCP connection but never answers the TLS handshake.
@pytest.mark.timeout(session.CONNECT_TIMEOUT + 5)
def test_unanswered_connect_fails_after_connect_timeout(
    django_db_blocker: pytest_django.DjangoDbBlocker, loopback_network: None
) -> None:
    with socket.create_server(("127.0.0.1", 0)) as listener:
        port = listener.getsockname()[1]
        handler = django.db.utils.ConnectionHandler(
            {"default": {"ENGINE": "dj_turso", "NAME": f"https://127.0.0.1:{port}"}}
        )
        connection = t.cast("base.DatabaseWrapper", handler["default"])
        with (
            django_db_blocker.unblock(),
            pytest.raises(django.db.OperationalError) as excinfo,
        ):
            connection.ensure_connection()

    assert utils.get_cause_chain(excinfo.value) == [
        django.db.OperationalError,
        connection.Database.OperationalError,
        protocol.ProtocolError,
        TimeoutError,
    ]
    # The ssl module words the timeout with its own source line, which varies by
    # Python build.
    timeout_error: BaseException = excinfo.value
    while timeout_error.__cause__ is not None:
        timeout_error = timeout_error.__cause__
    assert str(excinfo.value) == f"request to /v3/pipeline failed: {timeout_error}"


# As upstream's urllib.request.urlopen, through the proxy the environment names, by
# URL or by host and port alone.
@pytest.mark.parametrize(
    "proxy_template",
    [
        pytest.param("http://127.0.0.1:{port}", id="url"),
        pytest.param("127.0.0.1:{port}", id="authority"),
    ],
)
def test_http_proxy_from_environment_carries_queries(
    django_db_blocker: pytest_django.DjangoDbBlocker,
    hrana_stub: utils.HranaStub,
    monkeypatch: pytest.MonkeyPatch,
    proxy: utils.CountingProxy,
    proxy_template: str,
) -> None:
    for name in ("HTTP_PROXY", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("http_proxy", proxy_template.format(port=proxy.port))
    handler = django.db.utils.ConnectionHandler(
        {
            "default": {
                "ENGINE": "dj_turso",
                "NAME": hrana_stub.url,
                "OPTIONS": {"auth_token": hrana_stub.auth_token},
            }
        }
    )
    connection = t.cast("base.DatabaseWrapper", handler["default"])

    with django_db_blocker.unblock():
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            rows = cursor.fetchall()
        connection.close()

    assert rows == [(1,)]
    # Setup, the query, then the close.
    assert proxy.received_request_count == 3


# Port 1 refuses connections: only a bypassed proxy lets the query through.
def test_no_proxy_from_environment_bypasses_proxy(
    django_db_blocker: pytest_django.DjangoDbBlocker,
    hrana_stub: utils.HranaStub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("HTTP_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    handler = django.db.utils.ConnectionHandler(
        {
            "default": {
                "ENGINE": "dj_turso",
                "NAME": hrana_stub.url,
                "OPTIONS": {"auth_token": hrana_stub.auth_token},
            }
        }
    )
    connection = t.cast("base.DatabaseWrapper", handler["default"])

    with django_db_blocker.unblock():
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            rows = cursor.fetchall()
        connection.close()

    assert rows == [(1,)]


def test_http_proxy_credentials_from_environment_authorize_queries(
    django_db_blocker: pytest_django.DjangoDbBlocker,
    hrana_stub: utils.HranaStub,
    monkeypatch: pytest.MonkeyPatch,
    proxy: utils.CountingProxy,
) -> None:
    for name in ("HTTP_PROXY", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("http_proxy", f"http://us%40er:p%3Ass@127.0.0.1:{proxy.port}")
    handler = django.db.utils.ConnectionHandler(
        {
            "default": {
                "ENGINE": "dj_turso",
                "NAME": hrana_stub.url,
                "OPTIONS": {"auth_token": hrana_stub.auth_token},
            }
        }
    )
    connection = t.cast("base.DatabaseWrapper", handler["default"])

    with django_db_blocker.unblock():
        connection.ensure_connection()
        connection.close()

    assert [
        utils.get_request_headers(request).get("Proxy-Authorization")
        for request in proxy.received_requests
    ] == ["Basic dXNAZXI6cDpzcw==", "Basic dXNAZXI6cDpzcw=="]


def test_https_proxy_from_environment_tunnels_queries(
    cloud_settings: dict[str, t.Any],
    django_db_blocker: pytest_django.DjangoDbBlocker,
    monkeypatch: pytest.MonkeyPatch,
    tunnel_proxy: utils.TunnelProxy,
) -> None:
    for name in ("HTTPS_PROXY", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(
        "https_proxy", f"http://us%40er:p%3Ass@127.0.0.1:{tunnel_proxy.port}"
    )
    handler = django.db.utils.ConnectionHandler({"default": cloud_settings})
    connection = t.cast("base.DatabaseWrapper", handler["default"])

    with django_db_blocker.unblock():
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            rows = cursor.fetchall()
        connection.close()

    host = urllib.parse.urlsplit(cloud_settings["NAME"]).hostname
    assert rows == [(1,)]
    assert tunnel_proxy.received_requests == [
        f"CONNECT {host}:443 HTTP/1.1\r\n"
        f"Proxy-Authorization: Basic dXNAZXI6cDpzcw==\r\n"
        f"Host: {host}:443\r\n\r\n".encode()
    ]


def test_atomic_block_sends_begin_with_first_statement(
    proxied_alias: str, proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    with proxied_target.cursor() as cursor:
        cursor.execute("CREATE TABLE item (name TEXT)")

    with transaction.atomic(using=proxied_alias), proxied_target.cursor() as cursor:
        cursor.execute("INSERT INTO item VALUES ('a')")
    with proxied_target.cursor() as cursor:
        cursor.execute("SELECT name FROM item")
        rows = cursor.fetchall()

    assert rows == [("a",)]
    # Setup, CREATE, BEGIN with INSERT, COMMIT, then SELECT.
    assert proxy.received_request_count == 5


def test_nested_atomic_block_sends_savepoints_with_neighbors(
    proxied_alias: str, proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    with proxied_target.cursor() as cursor:
        cursor.execute("CREATE TABLE item (name TEXT)")

    with (
        transaction.atomic(using=proxied_alias),
        transaction.atomic(using=proxied_alias),
        proxied_target.cursor() as cursor,
    ):
        cursor.execute("INSERT INTO item VALUES ('a')")
    with proxied_target.cursor() as cursor:
        cursor.execute("SELECT name FROM item")
        rows = cursor.fetchall()

    assert rows == [("a",)]
    # Setup, CREATE, BEGIN and SAVEPOINT with INSERT, RELEASE with COMMIT, then
    # SELECT.
    assert proxy.received_request_count == 5


def test_empty_atomic_block_sends_one_request(
    proxied_alias: str, proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    proxied_target.ensure_connection()

    with transaction.atomic(using=proxied_alias):
        pass

    # Setup, then BEGIN with COMMIT.
    assert proxy.received_request_count == 2


@pytest.mark.parametrize(
    "statements",
    [
        pytest.param(["begin"], id="lowercase"),
        pytest.param(["BEGIN IMMEDIATE TRANSACTION"], id="immediate"),
        pytest.param(["BEGIN", ' SAVEPOINT "s1" ;'], id="savepoint"),
        pytest.param(["BEGIN", 'SAVEPOINT "s1"', 'RELEASE "s1"'], id="release"),
    ],
)
def test_transaction_statements_wait_for_next_query(
    proxied_target: base.DatabaseWrapper,
    proxy: utils.CountingProxy,
    statements: list[str],
) -> None:
    proxied_target.ensure_connection()

    with proxied_target.cursor() as cursor:
        for sql in statements:
            cursor.execute(sql)
        sent_after_statements = proxy.received_request_count
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()

    assert rows == [(1,)]
    assert sent_after_statements == 1
    assert proxied_target.connection.in_transaction is True
    assert proxy.received_request_count == 2


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("/* BEGIN */ SELECT 1", id="comment"),
        pytest.param("SELECT 'BEGIN'", id="literal"),
        pytest.param('SAVEPOINT "s1"', id="savepoint-outside-transaction"),
    ],
)
def test_other_statement_is_sent_at_once(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy, sql: str
) -> None:
    proxied_target.ensure_connection()

    with proxied_target.cursor() as cursor:
        cursor.execute(sql)

    assert proxy.received_request_count == 2


def test_failed_held_statement_raises_at_next_query_and_skips_it(
    proxied_target: base.DatabaseWrapper,
) -> None:
    with proxied_target.cursor() as cursor:
        cursor.execute("CREATE TABLE item (name TEXT)")
        cursor.execute("BEGIN")
        cursor.execute('RELEASE SAVEPOINT "missing"')
        with pytest.raises(django.db.OperationalError) as excinfo:
            cursor.execute("INSERT INTO item VALUES ('a')")
        cursor.execute("SELECT name FROM item")
        rows = cursor.fetchall()
        proxied_target.rollback()

    assert str(excinfo.value) == (
        "Tursodb error: Transaction error: no such savepoint: missing"
    )
    assert rows == []


def test_executescript_commits_held_transaction_first(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    proxied_target.ensure_connection()

    with proxied_target.cursor() as cursor:
        cursor.execute("BEGIN")
        cursor.executescript("SELECT 1;")

    assert proxied_target.connection.in_transaction is False
    # Setup, BEGIN with COMMIT, then the script.
    assert proxy.received_request_count == 3


def test_failed_request_carrying_held_begin_loses_transaction(
    proxied_alias: str, proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    with proxied_target.cursor() as cursor:
        cursor.execute("CREATE TABLE item (name TEXT)")

    with transaction.atomic(using=proxied_alias), proxied_target.cursor() as cursor:
        proxy.fail_next_request()
        with pytest.raises(django.db.OperationalError) as excinfo:
            cursor.execute("INSERT INTO item VALUES ('a')")
        with pytest.raises(django.db.OperationalError) as later_excinfo:
            cursor.execute("INSERT INTO item VALUES ('b')")
        transaction.set_rollback(True, using=proxied_alias)
    with proxied_target.cursor() as cursor:
        cursor.execute("SELECT name FROM item")
        rows = cursor.fetchall()

    assert str(excinfo.value) == (
        "Lost the connection to the remote database inside a transaction: whether "
        "it committed is unknown, and this connection cannot be used again."
    )
    assert str(later_excinfo.value) == str(excinfo.value)
    assert rows == []


def test_held_begin_refused_by_server_keeps_connection_usable(
    httpserver: pytest_httpserver.HTTPServer, stub_target: base.DatabaseWrapper
) -> None:
    stub_target.ensure_connection()
    httpserver.expect_oneshot_request("/v3/cursor").respond_with_data(
        '{"baton": null, "base_url": null}\n'
        '{"type": "step_error", "step": 0, "error": '
        '{"message": "database is locked", "code": "SQLITE_BUSY"}}\n'
        '{"type": "step_begin", "step": 2, "cols": []}\n'
        '{"type": "step_end", "affected_row_count": 0, "last_insert_rowid": null}\n'
    )

    with stub_target.cursor() as cursor:
        cursor.execute("BEGIN IMMEDIATE")
        with pytest.raises(django.db.OperationalError) as excinfo:
            cursor.execute("SELECT 1")

    assert str(excinfo.value) == "database is locked"
    assert stub_target.is_usable() is True
    assert stub_target.connection.in_transaction is False


def test_idle_atomic_block_before_first_statement_runs_on_new_stream(
    proxied_alias: str, proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    with proxied_target.cursor() as cursor:
        cursor.execute("CREATE TABLE item (name TEXT)")

    with transaction.atomic(using=proxied_alias), proxied_target.cursor() as cursor:
        time.sleep(session.STREAM_IDLE_TIMEOUT + SECONDS_PAST_STREAM_EXPIRY)
        cursor.execute("INSERT INTO item VALUES ('a')")
    with proxied_target.cursor() as cursor:
        cursor.execute("SELECT name FROM item")
        rows = cursor.fetchall()

    assert rows == [("a",)]
    # Setup, CREATE, the idle stream's close, setup again, BEGIN with INSERT, COMMIT,
    # then SELECT.
    assert proxy.received_request_count == 7


@pytest.mark.usefixtures("item_table")
def test_nested_atomic_blocks_commit_and_roll_back_savepoints(
    driver_alias: str, driver_target: base.DatabaseWrapper
) -> None:
    with driver_target.cursor() as cursor:
        cursor.execute("CREATE TABLE item (name TEXT)")

    with transaction.atomic(using=driver_alias), driver_target.cursor() as cursor:
        with transaction.atomic(using=driver_alias):
            cursor.execute("INSERT INTO item VALUES ('kept')")
        with transaction.atomic(using=driver_alias):
            cursor.execute("INSERT INTO item VALUES ('discarded')")
            transaction.set_rollback(True, using=driver_alias)
    with driver_target.cursor() as cursor:
        cursor.execute("SELECT name FROM item")
        rows = cursor.fetchall()

    assert rows == [("kept",)]


def test_release_ending_savepoint_transaction_keeps_later_errors(
    proxied_target: base.DatabaseWrapper,
) -> None:
    with proxied_target.cursor() as cursor:
        cursor.execute("CREATE TABLE item (name TEXT UNIQUE)")
        cursor.execute('SAVEPOINT "s1"')
        cursor.execute("INSERT INTO item VALUES ('a')")
        cursor.execute('RELEASE "s1"')
        with pytest.raises(django.db.IntegrityError):
            cursor.execute("INSERT INTO item VALUES ('a')")
        cursor.execute("SELECT name FROM item")
        rows = cursor.fetchall()

    assert rows == [("a",)]
    assert proxied_target.is_usable() is True


def test_statement_after_held_statements_reports_its_own_result(
    proxied_alias: str, proxied_target: base.DatabaseWrapper
) -> None:
    with proxied_target.cursor() as cursor:
        cursor.execute("CREATE TABLE item (pk INTEGER PRIMARY KEY, name TEXT)")

    with (
        transaction.atomic(using=proxied_alias),
        transaction.atomic(using=proxied_alias),
        proxied_target.cursor() as cursor,
    ):
        cursor.execute("INSERT INTO item (name) VALUES ('a')")
        lastrowid = cursor.lastrowid
        rowcount = cursor.rowcount
    with transaction.atomic(using=proxied_alias), proxied_target.cursor() as cursor:
        cursor.execute("SELECT pk, name FROM item")
        description = [column[0] for column in cursor.description]
        rows = cursor.fetchall()

    assert lastrowid == 1
    assert rowcount == 1
    assert description == ["pk", "name"]
    assert rows == [(1, "a")]


def test_fatal_error_after_held_begin_loses_transaction(
    httpserver: pytest_httpserver.HTTPServer, stub_target: base.DatabaseWrapper
) -> None:
    stub_target.ensure_connection()
    httpserver.expect_oneshot_request("/v3/cursor").respond_with_data(
        '{"baton": "baton", "base_url": null}\n'
        '{"type": "step_begin", "step": 0, "cols": []}\n'
        '{"type": "step_end", "affected_row_count": 0, "last_insert_rowid": null}\n'
        '{"type": "error", "error": {"message": "cursor failed"}}\n'
    )

    with stub_target.cursor() as cursor:
        cursor.execute("BEGIN")
        with pytest.raises(django.db.OperationalError) as excinfo:
            cursor.execute("SELECT 1")

    assert str(excinfo.value) == (
        "Lost the connection to the remote database inside a transaction: whether "
        "it committed is unknown, and this connection cannot be used again."
    )
    assert stub_target.is_usable() is False


@pytest.mark.parametrize(
    ("body", "message"),
    [
        pytest.param(
            "\n",
            "cursor response body ended before the cursor response line",
            id="empty",
        ),
        pytest.param(
            "not json\n",
            "invalid cursor response: Expecting value: line 1 column 1 (char 0)",
            id="response",
        ),
        pytest.param(
            '{"baton": null}\nnot json\n',
            "invalid cursor entry: Expecting value: line 1 column 1 (char 0)",
            id="entry",
        ),
        pytest.param(
            '{"baton": null}\n{"type": "step_begin", "step": 0, "cols": []}\n'
            '{"type": "step_end", "affected_row_count": 1, "last_insert_rowid": [1]}\n',
            "invalid rowid in server response: int() argument must be a string, a "
            "bytes-like object or a real number, not 'list'",
            id="rowid",
        ),
        pytest.param(
            '{"baton": null}\n{"type": "step_error", "step": 0, "error": {}}\n',
            "unknown error",
            id="error-without-message",
        ),
    ],
)
def test_malformed_cursor_response_raises_operational_error(
    body: str,
    httpserver: pytest_httpserver.HTTPServer,
    message: str,
    stub_target: base.DatabaseWrapper,
) -> None:
    stub_target.ensure_connection()
    httpserver.expect_oneshot_request("/v3/cursor").respond_with_data(body)

    with (
        stub_target.cursor() as cursor,
        pytest.raises(django.db.OperationalError) as excinfo,
    ):
        cursor.execute("INSERT INTO item VALUES (1)")

    assert str(excinfo.value) == message
