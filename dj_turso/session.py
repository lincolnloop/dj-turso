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

# Seconds a server stream outlives its last request, per Turso's HTTP API docs.
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


# Transaction statements that return nothing: held, and sent as the first steps of
# the next statement's request, to save their round trips.
BEGIN_REGEX = re.compile(
    r"\s*BEGIN(?:\s+(?:DEFERRED|IMMEDIATE|EXCLUSIVE))?(?:\s+TRANSACTION)?\s*;?\s*",
    re.IGNORECASE,
)
SAVEPOINT_REGEX = re.compile(
    r"\s*(?:SAVEPOINT|RELEASE(?:\s+SAVEPOINT)?)\s+\S+\s*;?\s*", re.IGNORECASE
)

# Turso Cloud's answer to a statement on a transaction it rolled back for idling.
IDLE_ROLLBACK_MESSAGE = (
    "interactive transaction was rolled back because the stream was idle for too long"
)


class LostTransactionError(
    exceptions.TursoBackendError, turso_serverless.OperationalError
):
    def __init__(self, *, rolled_back: bool = False) -> None:
        if rolled_back:
            loss = "The remote database rolled back the transaction for idling"
            outcome = "it did not commit"
        else:
            loss = "Lost the connection to the remote database inside a transaction"
            outcome = "whether it committed is unknown"
        super().__init__(
            f"{loss}: {outcome}, and this connection cannot be used again."
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
        self.transaction_rolled_back = False
        self.setup_statements: list[str] = []
        self.foreign_keys_statement: str | None = None
        self.starting_stream = False
        self.stream_was_reset = False
        self.last_response_time = time.monotonic()
        self.held_statements: list[str] = []
        # Whether the open transaction began with BEGIN: only then can no RELEASE
        # end it, and savepoint statements are held too.
        self.transaction_began = False
        self.held_begin_refused = False

    # The driver reads a transaction as open from this, and skips COMMIT and
    # ROLLBACK otherwise.
    @property
    def autocommit(self) -> bool:
        return self._autocommit and not self.held_statements

    # A failed statement that leaves no transaction behind the one it ran in lost
    # it, however the stream ended; so did one that succeeded on a stream that ended.
    def execute_stmt(
        self,
        sql: str,
        args: list[t.Any] | None = None,
        named_args: list[tuple[str, t.Any]] | None = None,
        want_rows: bool = True,
    ) -> serverless_session.StmtResult:
        # The driver raises a parameter it cannot encode as bare TypeError or
        # ValueError, which Django would not wrap; stdlib sqlite3 raises
        # ProgrammingError.
        try:
            protocol.build_batch_step(sql, args, named_args)
        except (TypeError, ValueError) as exc:
            raise turso_serverless.ProgrammingError(str(exc)) from exc
        if self.autocommit:
            self.transaction_began = False
        if args is None and named_args is None:
            if BEGIN_REGEX.fullmatch(sql):
                self.transaction_began = True
                self.held_statements.append(sql)
                return get_empty_result()
            if self.transaction_began and SAVEPOINT_REGEX.fullmatch(sql):
                self.held_statements.append(sql)
                return get_empty_result()
        # A held BEGIN counts: once sent, whether it ran is known only from the answer.
        in_transaction = not self.autocommit
        self.stream_was_reset = False
        self.held_begin_refused = False
        try:
            result = self.execute_batch(sql, args, named_args, want_rows)
        except RuntimeError as exc:
            if in_transaction and self._autocommit and not self.held_begin_refused:
                self.transaction_lost = True
                self.transaction_rolled_back = (
                    isinstance(exc, protocol.ServerError)
                    and exc.code == "SQLITE_BUSY"
                    and IDLE_ROLLBACK_MESSAGE in str(exc)
                )
                raise LostTransactionError(
                    rolled_back=self.transaction_rolled_back
                ) from exc
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

    # Upstream's execute_stmt, with the held statements as the batch's first steps,
    # each step run only if the one before it succeeded.
    def execute_batch(
        self,
        sql: str,
        args: list[t.Any] | None,
        named_args: list[tuple[str, t.Any]] | None,
        want_rows: bool,
    ) -> serverless_session.StmtResult:
        held_statements, self.held_statements = self.held_statements, []
        steps = [
            protocol.build_batch_step(held_sql, want_rows=False)
            for held_sql in held_statements
        ]
        steps.append(
            protocol.build_batch_step(
                sql, args=args, named_args=named_args, want_rows=want_rows
            )
        )
        for index, step in enumerate(steps[1:], start=1):
            step["condition"] = {"type": "ok", "step": index - 1}
        steps.append(self._autocommit_probe_step())
        raw = self._post(
            "/v3/cursor", {"baton": self._baton, "batch": {"steps": steps}}
        )
        decoder = CursorDecoder(statement_step=len(steps) - 2)
        try:
            cursor_response, entry_lines = parse_cursor_body(raw)
            self._update_stream(
                cursor_response.get("baton"), cursor_response.get("base_url")
            )
            for line in entry_lines:
                decoder.feed(parse_json_line(line, "invalid cursor entry"))
                if decoder.fatal is not None:
                    break
        except protocol.ProtocolError:
            self._reset_stream()
            raise
        # A held BEGIN the server refused left no transaction to lose.
        self.held_begin_refused = decoder.failed_step is not None and any(
            BEGIN_REGEX.fullmatch(held_sql)
            for held_sql in held_statements[: decoder.failed_step + 1]
        )
        if decoder.fatal is not None:
            self._refresh_autocommit()
            raise decoder.step_error or decoder.fatal
        if decoder.probe_unreliable:
            self._refresh_autocommit()
        else:
            self._autocommit = decoder.probe_executed
        if decoder.step_error is not None:
            raise decoder.step_error
        return decoder.result

    def _reset_stream(self) -> None:
        super()._reset_stream()
        self.stream_was_reset = True

    # Like a broken PostgreSQL connection, nothing runs again until Django closes it.
    def _post(self, path: str, body: dict[str, t.Any]) -> bytes:
        if self.transaction_lost:
            raise LostTransactionError(rolled_back=self.transaction_rolled_back)
        # Outside a transaction, an idle stream is replaced, not sent to: Turso Cloud
        # may have forgotten it. Inside one, Turso Cloud answers if it rolled it back.
        if (
            self._baton is not None
            and self._autocommit
            and time.monotonic() - self.last_response_time
            > STREAM_IDLE_TIMEOUT - STREAM_IDLE_MARGIN
        ):
            self.close_stream()
            # Nothing was sent to it: no transaction of its own was lost.
            self.stream_was_reset = False
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
                    raise get_server_error(result["error"])
        except RuntimeError:
            self.close_stream()
            raise
        finally:
            self.starting_stream = False
        self.last_response_time = time.monotonic()

    def close(self) -> None:
        self.held_statements = []
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


# Upstream's cursor decoder, for a batch of any length: the statement's result, the
# failed step's error, and whether the trailing autocommit probe ran.
class CursorDecoder:
    def __init__(self, statement_step: int) -> None:
        self.statement_step = statement_step
        self.probe_step = statement_step + 1
        self.current_step: int | None = None
        self.result = serverless_session.StmtResult(
            columns=[], rows=[], affected_rows=0, last_insert_rowid=None
        )
        self.step_error: protocol.ServerError | None = None
        self.failed_step: int | None = None
        self.fatal: protocol.ServerError | None = None
        self.probe_executed = False
        self.probe_unreliable = False

    def feed(self, entry: dict[str, t.Any]) -> None:
        match entry.get("type"):
            case "step_begin":
                self.current_step = entry.get("step")
                if self.current_step == self.probe_step:
                    self.probe_executed = True
                elif self.current_step == self.statement_step:
                    self.result.columns = [
                        column.get("name") or "" for column in entry.get("cols") or []
                    ]
            case "row" if self.current_step == self.statement_step:
                self.result.rows.append(
                    tuple(
                        protocol.decode_value(value) for value in entry.get("row") or []
                    )
                )
            case "step_end" if self.current_step == self.statement_step:
                self.result.affected_rows = entry.get("affected_row_count") or 0
                self.result.last_insert_rowid = get_rowid(entry)
            case "step_error":
                if entry.get("step") == self.probe_step:
                    self.probe_unreliable = True
                else:
                    self.failed_step = entry.get("step")
                    self.step_error = get_server_error(entry.get("error"))
                self.current_step = None
            case "error":
                self.fatal = get_server_error(entry.get("error"))


class KeepAliveConnection(turso_serverless.Connection):
    def __init__(self, session: KeepAliveSession, isolation_level: str | None) -> None:
        super().__init__(session, isolation_level=isolation_level)
        self.session = session

    # The driver skips COMMIT and ROLLBACK once the stream reads autocommit; a lost
    # transaction must fail both, as a broken PostgreSQL connection does, so Django
    # closes this one.
    def commit(self) -> None:
        if self.session.transaction_lost:
            raise LostTransactionError(rolled_back=self.session.transaction_rolled_back)
        super().commit()

    def rollback(self) -> None:
        if self.session.transaction_lost:
            raise LostTransactionError(rolled_back=self.session.transaction_rolled_back)
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


def get_empty_result() -> serverless_session.StmtResult:
    return serverless_session.StmtResult(
        columns=[], rows=[], affected_rows=0, last_insert_rowid=None
    )


# As upstream, tolerating missing fields.
def get_server_error(error: dict[str, t.Any] | None) -> protocol.ServerError:
    error = error if isinstance(error, dict) else {}
    message = error.get("message")
    return protocol.ServerError(
        message if isinstance(message, str) else "unknown error",
        code=error.get("code"),
        extended_code=error.get("extended_code"),
    )


def get_rowid(step_end: dict[str, t.Any]) -> int | None:
    rowid = step_end.get("last_insert_rowid")
    if rowid is None:
        return None
    try:
        return int(rowid)
    except (TypeError, ValueError) as exc:
        message = f"invalid rowid in server response: {exc}"
        raise protocol.ProtocolError(message) from exc


# The cursor response line, parsed, and the entry lines after it.
def parse_cursor_body(raw: bytes) -> tuple[dict[str, t.Any], list[str]]:
    lines = [line for line in raw.decode().splitlines() if line.strip()]
    if not lines:
        message = "cursor response body ended before the cursor response line"
        raise protocol.ProtocolError(message)
    return parse_json_line(lines[0], "invalid cursor response"), lines[1:]


def parse_json_line(line: str, label: str) -> dict[str, t.Any]:
    try:
        parsed: dict[str, t.Any] = json.loads(line)
    except ValueError as exc:
        message = f"{label}: {exc}"
        raise protocol.ProtocolError(message) from exc
    return parsed


def connect_with_untimed_reads(connection: http.client.HTTPConnection) -> None:
    connection.connect()
    connection.sock.settimeout(None)


def is_closed_by_server(connection: http.client.HTTPConnection) -> bool:
    if connection.sock is None:
        return False
    with selectors.DefaultSelector() as selector:
        selector.register(connection.sock, selectors.EVENT_READ)
        return bool(selector.select(timeout=0))


# The server's JSON "error" or "message", as upstream reports a non-200 status; bodies
# that are not JSON carry none.
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
