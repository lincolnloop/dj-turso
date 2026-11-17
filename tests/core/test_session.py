import http.client

import django.db
import pytest
from turso_serverless import protocol

from dj_turso import base, session
from tests import utils


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
        f"request to http://127.0.0.1:{proxy.port}/v3/cursor failed: "
        "Remote end closed connection without response"
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
