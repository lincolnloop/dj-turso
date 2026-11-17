import collections.abc
import http
import http.client
import os
import resource
import socket
import time
import typing as t

import django.db
import django.db.utils
import pytest
import pytest_django
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

    assert str(excinfo.value) == "SQLite error: no such table: missing"
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

    assert proxied_target.connection is None


def test_lost_transaction_fails_every_later_statement_unsent(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    with proxied_target.cursor() as cursor:
        cursor.execute("BEGIN")
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
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    with proxied_target.cursor() as cursor:
        cursor.execute("BEGIN")
        proxy.answer_next_requests(b"not json\n")
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
    ]
    assert proxied_target.is_usable() is False


# A cursor error makes the driver ask for the transaction state in a pipeline, whose
# malformed answer ends the stream.
def test_malformed_state_answer_inside_transaction_loses_transaction(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    with proxied_target.cursor() as cursor:
        cursor.execute("BEGIN")
        proxy.answer_next_requests(
            b'{"baton": "baton", "base_url": null}\n'
            b'{"type": "error", "error": {"message": "cursor failed"}}\n',
            b"not json",
        )
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
    assert proxied_target.is_usable() is False


def test_new_stream_repeats_session_setup(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    proxied_target.settings_dict["OPTIONS"]["init_command"] = (
        "PRAGMA defer_foreign_keys = ON"
    )
    proxied_target.ensure_connection()
    proxy.fail_next_request()

    with proxied_target.cursor() as cursor:
        with pytest.raises(django.db.OperationalError):
            cursor.execute("SELECT 1")
        cursor.execute("PRAGMA foreign_keys")
        foreign_keys = cursor.fetchall()
        cursor.execute("PRAGMA defer_foreign_keys")
        defer_foreign_keys = cursor.fetchall()

    assert foreign_keys == [(1,)]
    assert defer_foreign_keys == [(1,)]
    # Setup, the failed query, setup again, then each query once.
    assert proxy.received_request_count == 5


def test_failed_setup_of_new_stream_is_not_used_by_next_query(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    proxied_target.ensure_connection()
    proxy.fail_next_request()

    with proxied_target.cursor() as cursor:
        with pytest.raises(django.db.OperationalError):
            cursor.execute("SELECT 1")
        proxy.answer_next_requests(
            b'{"baton": "half-set-up", "base_url": null, "results": ['
            b'{"type": "error", "error": {"message": "setup failed"}}, '
            b'{"type": "ok", "response": {"type": "get_autocommit", '
            b'"is_autocommit": true}}]}'
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
    proxy.answer_next_requests(
        b'{"baton": null, "base_url": null, "results": ['
        b'{"type": "ok", "response": {"type": "execute", "result": {"cols": [], '
        b'"rows": [], "affected_row_count": 0, "last_insert_rowid": null}}}, '
        b'{"type": "ok", "response": {"type": "get_autocommit", "is_autocommit": true}}'
        b"]}"
    )
    proxied_target.ensure_connection()

    with proxied_target.cursor() as cursor:
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()

    assert rows == [(1,)]
    assert proxy.accepted_connection_count == 2


# Past where sqld expires a stream and Turso Cloud rolls back its transaction.
IDLE_PAST_STREAM_EXPIRY = 12.5


@pytest.mark.timeout(30)
def test_query_after_stream_expiry_starts_new_stream_unsent_to_old(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    with proxied_target.cursor() as cursor:
        cursor.execute("SELECT 1")
        time.sleep(IDLE_PAST_STREAM_EXPIRY)
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
        time.sleep(IDLE_PAST_STREAM_EXPIRY)
        cursor.execute("PRAGMA foreign_keys")
        rows = cursor.fetchall()

    assert rows == [(1,)]


@pytest.mark.timeout(30)
def test_close_after_stream_expiry_starts_no_new_stream(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    with proxied_target.cursor() as cursor:
        cursor.execute("SELECT 1")
    time.sleep(IDLE_PAST_STREAM_EXPIRY)

    proxied_target.close()

    # Setup, the query, then the close on the old stream.
    assert proxy.received_request_count == 3


@pytest.mark.timeout(30)
def test_stream_expiry_inside_transaction_loses_transaction_unsent(
    proxied_target: base.DatabaseWrapper, proxy: utils.CountingProxy
) -> None:
    with proxied_target.cursor() as cursor:
        cursor.execute("BEGIN")
        time.sleep(IDLE_PAST_STREAM_EXPIRY)
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
    # Setup, BEGIN, then the old stream's close.
    assert proxy.received_request_count == 3
    assert proxied_target.is_usable() is False


# sqld names the error "message" where Turso Cloud says "error"; as upstream, only
# a string counts.
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
    message: str,
    proxied_target: base.DatabaseWrapper,
    proxy: utils.CountingProxy,
) -> None:
    proxied_target.ensure_connection()
    proxy.answer_next_requests(body, status=http.HTTPStatus.BAD_REQUEST)

    with (
        proxied_target.cursor() as cursor,
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
    proxy.answer_next_requests(
        b'{"baton": null, "base_url": null, "results": ['
        b'{"type": "ok", "response": {"type": "execute", "result": {"cols": [], '
        b'"rows": [], "affected_row_count": 0, "last_insert_rowid": null}}}, '
        b'{"type": "ok", "response": {"type": "get_autocommit", "is_autocommit": true}}'
        b"]}"
    )
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
    django_db_blocker: pytest_django.DjangoDbBlocker,
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
