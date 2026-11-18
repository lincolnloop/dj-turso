"""Helpers shared by dj-turso's own test suites."""

import collections.abc
import contextlib
import dataclasses
import http
import itertools
import json
import pathlib
import re
import secrets
import socket
import threading
import time
import typing as t

import pytest_httpserver
import turso
from turso import _turso
from turso_serverless import protocol
from werkzeug import wrappers

# Turso Cloud's answer to each statement on a transaction it rolled back for idling.
IDLE_ROLLBACK_ERROR = {
    "message": (
        "SQLite error: interactive transaction was rolled back because the stream "
        "was idle for too long; retry the transaction"
    ),
    "code": "SQLITE_BUSY",
}

# Turso Cloud's code for an engine error, by pyturso's class for it: the driver raises
# SQLITE_CONSTRAINT as an integrity error. Turso Cloud sends SQL_INPUT_ERROR for a
# missing table, among others.
ERROR_CODES = {_turso.Constraint: "SQLITE_CONSTRAINT"}

# Turso Cloud counts the rows a statement changed, and the rowid it inserted, only
# for these.
CHANGING_STATEMENT_REGEX = re.compile(
    r"\s*(?:INSERT|REPLACE|UPDATE|DELETE)\b", re.IGNORECASE
)
INSERTING_STATEMENT_REGEX = re.compile(r"\s*(?:INSERT|REPLACE)\b", re.IGNORECASE)


# The exception, then each explicit cause it was raised from.
def get_cause_chain(exception: BaseException) -> list[type[BaseException]]:
    chain: list[type[BaseException]] = []
    current: BaseException | None = exception
    while current is not None:
        chain.append(type(current))
        current = current.__cause__
    return chain


# The answer to a new stream's setup pipeline: its foreign key pragma, then the
# transaction state.
def expect_stream_start(httpserver: pytest_httpserver.HTTPServer) -> None:
    httpserver.expect_oneshot_request("/v3/pipeline").respond_with_json(
        {
            "baton": "baton",
            "base_url": None,
            "results": [
                {
                    "type": "ok",
                    "response": {
                        "type": "execute",
                        "result": {
                            "cols": [],
                            "rows": [],
                            "affected_row_count": 0,
                            "last_insert_rowid": None,
                        },
                    },
                },
                {
                    "type": "ok",
                    "response": {"type": "get_autocommit", "is_autocommit": True},
                },
            ],
        }
    )


class CountingProxy:
    """A keep-alive front for an HTTP/1.1 server, counting what passes through.

    Holds each client connection open across requests, as Turso Cloud's servers do,
    and sends each request on a connection of its own to the server, which may close
    it. Reads each request whole (headers and Content-Length body) before forwarding
    it, so it counts requests exactly and can drop a connection on one that has fully
    reached it.
    """

    def __init__(self, upstream_host: str, upstream_port: int) -> None:
        self.upstream_address = (upstream_host, upstream_port)
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port: int = self.listener.getsockname()[1]
        self.condition = threading.Condition()
        self.accepted_connection_count = 0
        self.received_request_count = 0
        self.received_requests: list[bytes] = []
        self.open_sockets: set[socket.socket] = set()
        self.drop_next_request = False
        self.close_after_next_response = False
        self.response_delay = 0.0
        threading.Thread(target=self.accept_connections, daemon=True).start()

    def wait_for_open_connection_count(self, count: int) -> int:
        with self.condition:
            self.condition.wait_for(lambda: len(self.open_sockets) == count, timeout=5)
            return len(self.open_sockets)

    # Closes every client connection, as a server or load balancer does to idle ones.
    def drop_connections(self) -> None:
        with self.condition:
            clients = list(self.open_sockets)
        for client in clients:
            self.close_client(client)

    # The next request reaches the proxy whole, then its connection closes unanswered.
    def fail_next_request(self) -> None:
        with self.condition:
            self.drop_next_request = True

    # The server's next answer says it closes the connection, and then does.
    def close_after_next_answer(self) -> None:
        with self.condition:
            self.close_after_next_response = True

    # The server's next answer waits this long at the proxy before reaching the client.
    def delay_next_response(self, seconds: float) -> None:
        with self.condition:
            self.response_delay = seconds

    def close(self) -> None:
        self.listener.close()
        self.drop_connections()

    def accept_connections(self) -> None:
        while True:
            try:
                client, _ = self.listener.accept()
            except OSError:
                return
            with self.condition:
                self.accepted_connection_count += 1
                self.open_sockets.add(client)
            threading.Thread(
                target=self.forward_requests, args=(client,), daemon=True
            ).start()

    def forward_requests(self, client: socket.socket) -> None:
        buffer = b""
        with contextlib.suppress(OSError):
            while True:
                request, buffer = read_http_request(client, buffer)
                if request is None:
                    break
                with self.condition:
                    self.received_request_count += 1
                    self.received_requests.append(request)
                    dropping, self.drop_next_request = self.drop_next_request, False
                if dropping:
                    break
                with socket.create_connection(self.upstream_address) as upstream:
                    upstream.sendall(request)
                    response = b"".join(iter(lambda: upstream.recv(65536), b""))
                with self.condition:
                    delay, self.response_delay = self.response_delay, 0.0
                    closing = self.close_after_next_response
                    self.close_after_next_response = False
                time.sleep(delay)
                head, separator, body = response.partition(b"\r\n\r\n")
                if not closing:
                    head = head.replace(b"\r\nConnection: close", b"")
                client.sendall(head + separator + body)
                if closing:
                    break
        self.close_client(client)

    def close_client(self, client: socket.socket) -> None:
        with self.condition:
            if client not in self.open_sockets:
                return
            self.open_sockets.remove(client)
            self.condition.notify_all()
        with contextlib.suppress(OSError):
            client.shutdown(socket.SHUT_RDWR)
        client.close()


class TunnelProxy:
    """An HTTP proxy that only tunnels: it answers CONNECT, then relays raw bytes."""

    def __init__(self) -> None:
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port: int = self.listener.getsockname()[1]
        self.lock = threading.Lock()
        self.received_requests: list[bytes] = []
        self.open_sockets: list[socket.socket] = []
        threading.Thread(target=self.accept_connections, daemon=True).start()

    def close(self) -> None:
        self.listener.close()
        with self.lock:
            sockets = list(self.open_sockets)
        for sock in sockets:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            sock.close()

    def accept_connections(self) -> None:
        while True:
            try:
                client, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self.tunnel, args=(client,), daemon=True).start()

    def tunnel(self, client: socket.socket) -> None:
        request, rest = read_http_request(client, b"")
        if request is None:
            client.close()
            return
        host, _, port = request.split(b" ", 2)[1].decode().rpartition(":")
        upstream = socket.create_connection((host, int(port)))
        with self.lock:
            self.received_requests.append(request)
            self.open_sockets.extend((client, upstream))
        client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        upstream.sendall(rest)
        threading.Thread(target=relay, args=(upstream, client), daemon=True).start()
        relay(client, upstream)


@dataclasses.dataclass
class Stream:
    connection: turso.Connection
    answered_at: float = 0.0
    # Turso Cloud expires a stream idle after a pipeline request sooner than after a
    # cursor.
    expires_soon: bool = False


class HranaStub:
    """Turso Cloud's SQL over HTTP API, protocol version 3, as the driver uses it.

    Each stream is a pyturso connection to one database file, and an idle stream
    expires as Turso Cloud's does, after ``idle_timeout`` seconds. Requests carry
    ``auth_token``.
    """

    def __init__(
        self,
        httpserver: pytest_httpserver.HTTPServer,
        database_path: pathlib.Path,
        idle_timeout: float,
    ) -> None:
        self.database_path = database_path
        self.idle_timeout = idle_timeout
        self.url = httpserver.url_for("").rstrip("/")
        self.auth_token = secrets.token_urlsafe()
        self.lock = threading.Lock()
        self.server_id = secrets.token_hex(4)
        self.stream_numbers = itertools.count(1)
        self.streams: dict[str, Stream] = {}
        httpserver.expect_request("/v3/pipeline", method="POST").respond_with_handler(
            self.answer_pipeline
        )
        httpserver.expect_request("/v3/cursor", method="POST").respond_with_handler(
            self.answer_cursor
        )

    def close(self) -> None:
        for stream in self.streams.values():
            stream.connection.close()

    def answer_pipeline(self, request: wrappers.Request) -> wrappers.Response:
        return self.answer(request, self.run_pipeline)

    def answer_cursor(self, request: wrappers.Request) -> wrappers.Response:
        return self.answer(request, self.run_cursor)

    def answer(
        self,
        request: wrappers.Request,
        run: collections.abc.Callable[
            [str, Stream, dict[str, t.Any], bool], wrappers.Response
        ],
    ) -> wrappers.Response:
        with self.lock:
            refusal = self.refuse(request)
            if refusal is not None:
                return refusal
            body = json.loads(request.get_data())
            stream_id, stream, rolled_back = self.get_stream(body["baton"])
            if stream is None:
                return make_json_response(
                    {"error": f"stream not found: {stream_id}"},
                    http.HTTPStatus.NOT_FOUND,
                )
            return run(stream_id, stream, body, rolled_back)

    def run_pipeline(
        self, stream_id: str, stream: Stream, body: dict[str, t.Any], rolled_back: bool
    ) -> wrappers.Response:
        results = [
            self.run_pipeline_request(stream, pipeline_request, rolled_back)
            for pipeline_request in body["requests"]
        ]
        closing = rolled_back or body["requests"][-1]["type"] == "close"
        baton = self.finish_request(stream_id, stream, closing, expires_soon=True)
        return make_json_response(
            {"baton": baton, "base_url": None, "results": results}
        )

    def run_cursor(
        self, stream_id: str, stream: Stream, body: dict[str, t.Any], rolled_back: bool
    ) -> wrappers.Response:
        entries = []
        for index, step in enumerate(body["batch"]["steps"]):
            # The driver's only condition: whether the connection is in autocommit.
            if "condition" in step and stream.connection.in_transaction:
                continue
            result, error = self.run_statement(stream, step["stmt"], rolled_back)
            if error is not None:
                entries.append({"type": "step_error", "step": index, "error": error})
                continue
            entries.extend(
                [
                    {"type": "step_begin", "step": index, "cols": result["cols"]},
                    *({"type": "row", "row": row} for row in result["rows"]),
                    {
                        "type": "step_end",
                        "affected_row_count": result["affected_row_count"],
                        "last_insert_rowid": result["last_insert_rowid"],
                    },
                ]
            )
        baton = self.finish_request(stream_id, stream, rolled_back, expires_soon=False)
        # Turso Cloud ends the body without a replication_index terminator.
        lines = [{"baton": baton, "base_url": None}, *entries]
        return wrappers.Response(
            "".join(f"{json.dumps(line)}\n" for line in lines),
            content_type="application/json",
        )

    # Turso Cloud's answers to a request without a token, and with a wrong one.
    def refuse(self, request: wrappers.Request) -> wrappers.Response | None:
        authorization = request.headers.get("Authorization")
        if authorization is None:
            return make_json_response(
                {
                    "error": "Unauthorized: `unauthorized access attempt on database: "
                    "empty JWT token`"
                },
                http.HTTPStatus.UNAUTHORIZED,
            )
        if authorization != f"Bearer {self.auth_token}":
            return make_json_response(
                {"error": "JWT error: InvalidToken"}, http.HTTPStatus.BAD_REQUEST
            )
        return None

    # The stream a baton names, None once forgotten, and whether its transaction was
    # just rolled back for idling. Measured on Turso Cloud: after a pipeline request,
    # a transaction idle past the limit is rolled back and answered so, and its stream
    # forgotten by twice it; after a cursor, it lasts twice the limit, not thrice.
    def get_stream(self, baton: str | None) -> tuple[str, Stream | None, bool]:
        if baton is None:
            stream_id = f"{self.server_id}:{next(self.stream_numbers):x}"
            self.streams[stream_id] = Stream(
                turso.connect(str(self.database_path), isolation_level=None)
            )
            return stream_id, self.streams[stream_id], False
        stream_id = baton.rpartition(":")[0]
        stream = self.streams.get(stream_id)
        if stream is None:
            return stream_id, None, False
        idle = time.monotonic() - stream.answered_at
        if stream.expires_soon and (
            idle > 2 * self.idle_timeout
            or (idle > self.idle_timeout and not stream.connection.in_transaction)
        ):
            self.streams.pop(stream_id).connection.close()
            return stream_id, None, False
        rolled_back = stream.connection.in_transaction and idle > (
            self.idle_timeout if stream.expires_soon else 2.5 * self.idle_timeout
        )
        if rolled_back:
            stream.connection.execute("ROLLBACK")
        return stream_id, stream, rolled_back

    # The baton for the stream's next request, or None once closed.
    def finish_request(
        self, stream_id: str, stream: Stream, closing: bool, expires_soon: bool
    ) -> str | None:
        if closing:
            self.streams.pop(stream_id).connection.close()
            return None
        stream.answered_at = time.monotonic()
        stream.expires_soon = expires_soon
        return f"{stream_id}:{secrets.token_hex(2)}"

    # As upstream's own stub server, any other request gets a bare response of its type.
    def run_pipeline_request(
        self, stream: Stream, pipeline_request: dict[str, t.Any], rolled_back: bool
    ) -> dict[str, t.Any]:
        if pipeline_request["type"] == "execute":
            result, error = self.run_statement(
                stream, pipeline_request["stmt"], rolled_back
            )
            if error is not None:
                return {"type": "error", "error": error}
            last_insert_rowid = result["last_insert_rowid"]
            # A decimal string here, where the cursor endpoint sends a number.
            result["last_insert_rowid"] = (
                None if last_insert_rowid is None else str(last_insert_rowid)
            )
            return {"type": "ok", "response": {"type": "execute", "result": result}}
        if pipeline_request["type"] == "get_autocommit":
            return {
                "type": "ok",
                "response": {
                    "type": "get_autocommit",
                    "is_autocommit": not stream.connection.in_transaction,
                },
            }
        return {"type": "ok", "response": {"type": pipeline_request["type"]}}

    # A statement's result, or its error as Turso Cloud words it.
    def run_statement(
        self, stream: Stream, statement: dict[str, t.Any], rolled_back: bool
    ) -> tuple[dict[str, t.Any], dict[str, t.Any] | None]:
        if rolled_back:
            return {}, IDLE_ROLLBACK_ERROR
        # Named with or without the prefix the SQL gives them.
        named_args = {
            argument["name"].lstrip(":@$"): protocol.decode_value(argument["value"])
            for argument in statement.get("named_args", [])
        }
        args = [protocol.decode_value(value) for value in statement.get("args", [])]
        sql = statement["sql"]
        try:
            cursor = stream.connection.execute(sql, named_args or args)
            rows = cursor.fetchall()
        except turso.Error as exc:
            return {}, {
                "message": f"Tursodb error: {exc}",
                "code": ERROR_CODES.get(type(exc.__context__), "SQL_INPUT_ERROR"),
            }
        changes, last_insert_rowid = stream.connection.execute(
            "SELECT changes(), last_insert_rowid()"
        ).fetchone()
        changing = CHANGING_STATEMENT_REGEX.match(sql) is not None
        inserting = INSERTING_STATEMENT_REGEX.match(sql) is not None
        return {
            "cols": [
                {"name": column[0], "decltype": None}
                for column in cursor.description or []
            ],
            "rows": [
                [protocol.encode_value(value) for value in row]
                for row in (rows if statement.get("want_rows", True) else [])
            ],
            "affected_row_count": changes if changing else 0,
            "last_insert_rowid": last_insert_rowid if inserting else None,
        }, None


def relay(source: socket.socket, destination: socket.socket) -> None:
    with contextlib.suppress(OSError):
        for data in iter(lambda: source.recv(65536), b""):
            destination.sendall(data)
        destination.shutdown(socket.SHUT_WR)


# One request off the socket: the bytes, and what was read past it. None at EOF.
def read_http_request(sock: socket.socket, buffer: bytes) -> tuple[bytes | None, bytes]:
    while True:
        head, separator, rest = buffer.partition(b"\r\n\r\n")
        if separator and len(rest) >= (length := get_content_length(head)):
            return head + separator + rest[:length], rest[length:]
        if not (data := sock.recv(65536)):
            return None, buffer
        buffer += data


def get_content_length(head: bytes) -> int:
    return int(get_request_headers(head).get("Content-Length", "0"))


# The header fields of a request, from its head or the whole request.
def get_request_headers(request: bytes) -> dict[str, str]:
    head = request.partition(b"\r\n\r\n")[0].decode("latin-1")
    return dict(line.partition(": ")[::2] for line in head.split("\r\n")[1:])


def make_json_response(
    body: dict[str, t.Any], status: http.HTTPStatus = http.HTTPStatus.OK
) -> wrappers.Response:
    return wrappers.Response(
        json.dumps(body), status=status, content_type="application/json"
    )
