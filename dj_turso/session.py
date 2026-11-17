"""A turso_serverless session that keeps one HTTP connection open across requests."""

import http
import http.client
import json
import select
import typing as t
import urllib.parse

import turso_serverless
from turso_serverless import protocol
from turso_serverless import session as serverless_session

from dj_turso import exceptions


class LostTransactionError(
    exceptions.TursoBackendError, turso_serverless.OperationalError
):
    def __init__(self) -> None:
        super().__init__(
            "Lost the connection to the remote database inside a transaction: whether "
            "it committed is unknown, and this connection cannot be used again."
        )


# Overrides the private Session._post and reads _base_url, _autocommit and
# _reset_stream: tied to the turso-serverless range pinned in pyproject.toml.
class KeepAliveSession(serverless_session.Session):
    def __init__(self, url: str, auth_token: str | None = None) -> None:
        super().__init__(url, auth_token=auth_token)
        self.http_connection: http.client.HTTPConnection | None = None
        self.http_connection_origin: tuple[str, str] | None = None
        self.transaction_lost = False

    def _post(self, path: str, body: dict[str, t.Any]) -> bytes:
        in_transaction = not self._autocommit
        try:
            return self.post_on_connection(path, body)
        except protocol.ProtocolError as exc:
            if not in_transaction:
                raise
            self.transaction_lost = True
            raise LostTransactionError from exc

    def close(self) -> None:
        try:
            super().close()
        finally:
            self.close_http_connection()

    # Upstream's contract: any failure ends the stream and raises ProtocolError, and
    # a request is never sent twice.
    def post_on_connection(self, path: str, body: dict[str, t.Any]) -> bytes:
        url = f"{self._base_url}{path}"
        parts = urllib.parse.urlsplit(url)
        connection = self.get_http_connection(parts.scheme, parts.netloc)
        try:
            connection.request(
                "POST",
                parts.path,
                body=json.dumps(body, allow_nan=False).encode(),
                headers=self._headers(),
            )
            response = connection.getresponse()
            raw = response.read()
        except (http.client.HTTPException, OSError) as exc:
            self.close_http_connection()
            self._reset_stream()
            message = f"request to {url} failed: {exc}"
            raise protocol.ProtocolError(message) from exc
        if response.status != http.HTTPStatus.OK:
            self._reset_stream()
            raise protocol.ProtocolError(get_status_message(response.status, raw))
        return raw

    # Reconnects only before sending: a socket the server closed while idle reads
    # as ready, at EOF.
    def get_http_connection(
        self, scheme: str, netloc: str
    ) -> http.client.HTTPConnection:
        if self.http_connection is not None and (
            self.http_connection_origin != (scheme, netloc)
            or is_closed_by_server(self.http_connection)
        ):
            self.close_http_connection()
        if self.http_connection is None:
            connection_class = (
                http.client.HTTPSConnection
                if scheme == "https"
                else http.client.HTTPConnection
            )
            self.http_connection = connection_class(netloc)
            self.http_connection_origin = (scheme, netloc)
        return self.http_connection

    def close_http_connection(self) -> None:
        if self.http_connection is not None:
            self.http_connection.close()
            self.http_connection = None
            self.http_connection_origin = None


class KeepAliveConnection(turso_serverless.Connection):
    def __init__(self, session: KeepAliveSession, isolation_level: str | None) -> None:
        super().__init__(session, isolation_level=isolation_level)
        self.session = session


# Django's sqlite3 backend names the target "database"; turso_serverless, "url".
def connect(
    database: str, *, auth_token: str | None = None, isolation_level: str | None = None
) -> KeepAliveConnection:
    return KeepAliveConnection(
        KeepAliveSession(database, auth_token=auth_token), isolation_level
    )


def is_usable(connection: KeepAliveConnection) -> bool:
    return not connection.session.transaction_lost


def is_closed_by_server(connection: http.client.HTTPConnection) -> bool:
    return connection.sock is not None and bool(
        select.select([connection.sock], [], [], 0)[0]
    )


# The server's JSON "error", as upstream reports a non-200 status; bodies that are
# not JSON carry none.
def get_status_message(status: int, raw: bytes) -> str:
    try:
        error = json.loads(raw)["error"]
    except (KeyError, TypeError, ValueError):
        return f"HTTP status {status}"
    return f"HTTP status {status}: {error}"
