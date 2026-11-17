import collections.abc
import http.client
import os
import resource

import django.db
import pytest
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
