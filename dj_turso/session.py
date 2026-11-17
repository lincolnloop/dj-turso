"""A turso_serverless session that keeps one HTTP connection open across requests."""

import contextlib
import http
import http.client
import json
import selectors
import time
import typing as t
import urllib.parse

import turso_serverless
from turso_serverless import protocol
from turso_serverless import session as serverless_session

from dj_turso import exceptions

# Seconds a server stream outlives its last request, per Turso's HTTP API docs: sqld
# expires any stream, and Turso Cloud rolls back a transaction, after it.
STREAM_IDLE_TIMEOUT = 10

# The server's idle clock starts when its answer leaves, the client's when it lands:
# a stream is given up this many seconds early, for the round trip.
STREAM_IDLE_MARGIN = 1

# Seconds to resolve, connect and finish the TLS handshake; reads are untimed, so a
# long statement is never cut off.
CONNECT_TIMEOUT = 10


class LostTransactionError(
    exceptions.TursoBackendError, turso_serverless.OperationalError
):
    def __init__(self) -> None:
        super().__init__(
            "Lost the connection to the remote database inside a transaction: whether "
            "it committed is unknown, and this connection cannot be used again."
        )


# Overrides the private Session._post and reads _base_url, _baton, _autocommit and
# _reset_stream: tied to the turso-serverless range pinned in pyproject.toml.
class KeepAliveSession(serverless_session.Session):
    def __init__(self, url: str, auth_token: str | None = None) -> None:
        super().__init__(url, auth_token=auth_token)
        self.http_connection: http.client.HTTPConnection | None = None
        self.http_connection_origin: tuple[str, str] | None = None
        self.transaction_lost = False
        self.setup_statements: list[str] = []
        self.starting_stream = False
        self.last_response_time = time.monotonic()

    # A failed statement that leaves no transaction behind the one it ran in lost
    # it, however the stream ended.
    def execute_stmt(
        self,
        sql: str,
        args: list[t.Any] | None = None,
        named_args: list[tuple[str, t.Any]] | None = None,
        want_rows: bool = True,
    ) -> serverless_session.StmtResult:
        in_transaction = not self._autocommit
        try:
            return super().execute_stmt(sql, args, named_args, want_rows)
        except RuntimeError as exc:
            if in_transaction and self._autocommit:
                self.transaction_lost = True
                raise LostTransactionError from exc
            raise

    # Like a broken PostgreSQL connection, nothing runs again until Django closes it.
    def _post(self, path: str, body: dict[str, t.Any]) -> bytes:
        if self.transaction_lost:
            raise LostTransactionError
        # Never sent to an expired stream: sqld would refuse it, and Turso Cloud would
        # answer that it rolled the transaction back.
        if (
            self._baton is not None
            and time.monotonic() - self.last_response_time
            > STREAM_IDLE_TIMEOUT - STREAM_IDLE_MARGIN
        ):
            in_transaction = not self._autocommit
            self.close_stream()
            if in_transaction:
                self.transaction_lost = True
                raise LostTransactionError
        if self._baton is None and not self.starting_stream:
            self.start_stream()
            body = body | {"baton": self._baton}
        return self.post_on_connection(path, body)

    # Each server stream is a SQL connection of its own, so each one starts with the
    # session setup Django's connection asked for.
    def start_stream(self) -> None:
        self.starting_stream = True
        try:
            results = self.execute_pipeline(
                [
                    {"type": "execute", "stmt": {"sql": sql, "want_rows": False}}
                    for sql in self.setup_statements
                ]
            )
        finally:
            self.starting_stream = False
        self.last_response_time = time.monotonic()
        for result in results:
            if result["type"] == "error":
                error = result["error"]
                raise protocol.ServerError(
                    error["message"],
                    code=error.get("code"),
                    extended_code=error.get("extended_code"),
                )

    def close(self) -> None:
        try:
            self.close_stream()
        finally:
            self.close_http_connection()

    # Sent straight on the connection, so that an idle stream is not first replaced
    # by a new one. As upstream, errors are ignored: the stream may have expired.
    def close_stream(self) -> None:
        if self._baton is not None:
            with contextlib.suppress(protocol.ProtocolError):
                self.post_on_connection(
                    "/v3/pipeline",
                    {"baton": self._baton, "requests": [{"type": "close"}]},
                )
        self._reset_stream()

    # Upstream's contract: any failure ends the stream and raises ProtocolError, and
    # a request is never sent twice.
    def post_on_connection(self, path: str, body: dict[str, t.Any]) -> bytes:
        url = f"{self._base_url}{path}"
        parts = urllib.parse.urlsplit(url)
        try:
            connection = self.get_http_connection(parts.scheme, parts.netloc)
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
            # Not the URL: the driver reads constraint words in a message as an
            # integrity error, and a database URL may hold them.
            message = f"request to {path} failed: {exc}"
            raise protocol.ProtocolError(message) from exc
        if response.status != http.HTTPStatus.OK:
            self._reset_stream()
            raise protocol.ProtocolError(get_status_message(response.status, raw))
        self.last_response_time = time.monotonic()
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
            self.http_connection = connection_class(netloc, timeout=CONNECT_TIMEOUT)
            self.http_connection_origin = (scheme, netloc)
        # Connected here, not by http.client on request, so that every new socket,
        # even after the server closes one, waits on reads with no time limit.
        if self.http_connection.sock is None:
            connect_with_untimed_reads(self.http_connection)
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


# Starts the first stream now, as a local connection opens its file on connect. Its
# failures bypass the driver's error mapping, so they are filed here, as operational.
def set_up_session(connection: KeepAliveConnection, statements: list[str]) -> None:
    connection.session.setup_statements = statements
    try:
        connection.session.start_stream()
    except RuntimeError as exc:
        raise turso_serverless.OperationalError(str(exc)) from exc


def is_usable(connection: KeepAliveConnection) -> bool:
    return not connection.session.transaction_lost


def connect_with_untimed_reads(connection: http.client.HTTPConnection) -> None:
    connection.connect()
    connection.sock.settimeout(None)


def is_closed_by_server(connection: http.client.HTTPConnection) -> bool:
    if connection.sock is None:
        return False
    with selectors.DefaultSelector() as selector:
        selector.register(connection.sock, selectors.EVENT_READ)
        return bool(selector.select(timeout=0))


# The server's JSON "error" (Turso Cloud) or "message" (sqld), as upstream reports a
# non-200 status; bodies that are not JSON carry none.
def get_status_message(status: int, raw: bytes) -> str:
    try:
        parsed = json.loads(raw)
    except ValueError:
        return f"HTTP status {status}"
    if isinstance(parsed, dict):
        for key in ("error", "message"):
            if isinstance(parsed.get(key), str):
                return f"HTTP status {status}: {parsed[key]}"
    return f"HTTP status {status}"
