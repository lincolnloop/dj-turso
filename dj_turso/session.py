"""A turso_serverless session that keeps one HTTP connection open across requests."""

import base64
import contextlib
import http
import http.client
import json
import re
import selectors
import time
import typing as t
import urllib.parse
import urllib.request

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

# Sets the foreign key pragma, as Django's disable_constraint_checking() does; a read
# of it, PRAGMA foreign_keys alone, does not match.
FOREIGN_KEYS_ASSIGNMENT_REGEX = re.compile(
    r"\s*PRAGMA\s+(?:\w+\.)?foreign_keys\s*[=(]", re.IGNORECASE
)


class LostTransactionError(
    exceptions.TursoBackendError, turso_serverless.OperationalError
):
    def __init__(self) -> None:
        super().__init__(
            "Lost the connection to the remote database inside a transaction: whether "
            "it committed is unknown, and this connection cannot be used again."
        )


# Overrides the private Session._post and _reset_stream and reads _base_url, _baton
# and _autocommit: tied to the turso-serverless range pinned in pyproject.toml.
class KeepAliveSession(serverless_session.Session):
    def __init__(self, url: str, auth_token: str | None = None) -> None:
        super().__init__(url, auth_token=auth_token)
        self.http_connection: http.client.HTTPConnection | None = None
        self.http_connection_origin: tuple[str, str] | None = None
        # Set while the connection is to a proxy that forwards each request whole.
        self.forward_proxy_headers: dict[str, str] | None = None
        self.transaction_lost = False
        self.setup_statements: list[str] = []
        self.foreign_keys_statement: str | None = None
        self.starting_stream = False
        self.stream_was_reset = False
        self.last_response_time = time.monotonic()

    # A failed statement that leaves no transaction behind the one it ran in lost
    # it, however the stream ended; so did one that succeeded on a stream that ended.
    def execute_stmt(
        self,
        sql: str,
        args: list[t.Any] | None = None,
        named_args: list[tuple[str, t.Any]] | None = None,
        want_rows: bool = True,
    ) -> serverless_session.StmtResult:
        in_transaction = not self._autocommit
        self.stream_was_reset = False
        try:
            result = super().execute_stmt(sql, args, named_args, want_rows)
        except RuntimeError as exc:
            if in_transaction and self._autocommit:
                self.transaction_lost = True
                raise LostTransactionError from exc
            raise
        if in_transaction and self.stream_was_reset:
            self.transaction_lost = True
            raise LostTransactionError
        # Read back, not copied: inside a transaction SQLite ignores the pragma, and
        # Turso applies it.
        if FOREIGN_KEYS_ASSIGNMENT_REGEX.match(sql):
            enabled = self.execute_stmt("PRAGMA foreign_keys").rows[0][0]
            self.foreign_keys_statement = f"PRAGMA foreign_keys = {enabled}"
        return result

    def _reset_stream(self) -> None:
        super()._reset_stream()
        self.stream_was_reset = True

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
    # session setup Django's connection asked for, then the foreign key pragma as it
    # was last set.
    def start_stream(self) -> None:
        statements = self.setup_statements
        if self.foreign_keys_statement is not None:
            statements = [*statements, self.foreign_keys_statement]
        self.starting_stream = True
        try:
            results = self.execute_pipeline(
                [
                    {"type": "execute", "stmt": {"sql": sql, "want_rows": False}}
                    for sql in statements
                ]
            )
            for result in results:
                if result["type"] == "error":
                    error = result["error"]
                    raise protocol.ServerError(
                        error["message"],
                        code=error.get("code"),
                        extended_code=error.get("extended_code"),
                    )
        except RuntimeError:
            self.close_stream()
            raise
        finally:
            self.starting_stream = False
        self.last_response_time = time.monotonic()

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
            if self.forward_proxy_headers is None:
                target, headers = parts.path, self._headers()
            else:
                target, headers = url, self._headers() | self.forward_proxy_headers
            connection.request(
                "POST",
                target,
                body=json.dumps(body, allow_nan=False).encode(),
                headers=headers,
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
            proxy = get_proxy(scheme, netloc)
            if proxy is not None:
                proxy_netloc, proxy_headers = proxy
                self.http_connection = connection_class(
                    proxy_netloc, timeout=CONNECT_TIMEOUT
                )
                if scheme == "https":
                    self.http_connection.set_tunnel(netloc, headers=proxy_headers)
                else:
                    self.forward_proxy_headers = proxy_headers
            else:
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
            self.forward_proxy_headers = None


class KeepAliveConnection(turso_serverless.Connection):
    def __init__(self, session: KeepAliveSession, isolation_level: str | None) -> None:
        super().__init__(session, isolation_level=isolation_level)
        self.session = session

    # The driver skips COMMIT and ROLLBACK once the stream reads autocommit; a lost
    # transaction must fail both, as a broken PostgreSQL connection does, so Django
    # closes this one.
    def commit(self) -> None:
        if self.session.transaction_lost:
            raise LostTransactionError
        super().commit()

    def rollback(self) -> None:
        if self.session.transaction_lost:
            raise LostTransactionError
        super().rollback()


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
        connection.close()
        raise turso_serverless.OperationalError(str(exc)) from exc


def is_usable(connection: KeepAliveConnection) -> bool:
    return not connection.session.transaction_lost


# The proxy upstream's urllib.request.urlopen would take to the origin: its host and
# port, with the headers it needs, or None to connect directly.
def get_proxy(scheme: str, netloc: str) -> tuple[str, dict[str, str]] | None:
    proxy = urllib.request.getproxies().get(scheme)
    if not proxy or urllib.request.proxy_bypass(netloc):
        return None
    parts = urllib.parse.urlsplit(proxy if "://" in proxy else f"//{proxy}")
    headers = {}
    if parts.username and parts.password:
        credentials = (
            f"{urllib.parse.unquote(parts.username)}:"
            f"{urllib.parse.unquote(parts.password)}"
        )
        headers["Proxy-Authorization"] = (
            f"Basic {base64.b64encode(credentials.encode()).decode('ascii')}"
        )
    return urllib.parse.unquote(parts.netloc.rpartition("@")[2]), headers


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
