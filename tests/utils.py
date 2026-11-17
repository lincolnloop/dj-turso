"""Helpers shared by dj-turso's own test suites."""

import contextlib
import http
import socket
import threading
import time

# Signed by the test-only key whose public half compose.yaml gives the sqld service.
SQLD_JWT = (
    "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicncifQ."
    "n8r4qqfg1S8pfrOIl_C7EMdnotATmuPCsVYz-lXO9JUDcrEOYUVM-AadvNwy8GVE9gmT3g8DL_bfKk6QevR1CQ"
)


# The exception, then each explicit cause it was raised from.
def get_cause_chain(exception: BaseException) -> list[type[BaseException]]:
    chain: list[type[BaseException]] = []
    current: BaseException | None = exception
    while current is not None:
        chain.append(type(current))
        current = current.__cause__
    return chain


class CountingProxy:
    """A TCP forwarder in front of an HTTP/1.1 server, counting what passes through.

    Reads each request whole (headers and Content-Length body) before forwarding it,
    so it counts requests exactly and can drop a connection on one that has fully
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
        self.open_sockets: dict[socket.socket, socket.socket] = {}
        self.drop_next_request = False
        self.answers: list[tuple[http.HTTPStatus, bytes]] = []
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
            self.close_pair(client)

    # The next request reaches the proxy whole, then its connection closes unanswered.
    def fail_next_request(self) -> None:
        with self.condition:
            self.drop_next_request = True

    # The next requests reach the proxy whole and each gets one of these bodies, with
    # the status, in place of the server's answer; then its connection closes.
    def answer_next_requests(
        self, *bodies: bytes, status: http.HTTPStatus = http.HTTPStatus.OK
    ) -> None:
        with self.condition:
            self.answers.extend((status, body) for body in bodies)

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
            upstream = socket.create_connection(self.upstream_address)
            with self.condition:
                self.accepted_connection_count += 1
                self.open_sockets[client] = upstream
            threading.Thread(
                target=self.forward_requests, args=(client, upstream), daemon=True
            ).start()
            threading.Thread(
                target=self.forward_responses, args=(client, upstream), daemon=True
            ).start()

    def forward_requests(self, client: socket.socket, upstream: socket.socket) -> None:
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
                    answer = self.answers.pop(0) if self.answers else None
                if dropping:
                    break
                if answer is not None:
                    status, body = answer
                    client.sendall(
                        b"HTTP/1.1 %d %s\r\nConnection: close\r\n"
                        b"Content-Length: %d\r\n\r\n%s"
                        % (status, status.phrase.encode(), len(body), body)
                    )
                    break
                upstream.sendall(request)
        self.close_pair(client)

    def forward_responses(self, client: socket.socket, upstream: socket.socket) -> None:
        with contextlib.suppress(OSError):
            for data in iter(lambda: upstream.recv(65536), b""):
                with self.condition:
                    delay, self.response_delay = self.response_delay, 0.0
                time.sleep(delay)
                client.sendall(data)
        self.close_pair(client)

    def close_pair(self, client: socket.socket) -> None:
        with self.condition:
            upstream = self.open_sockets.pop(client, None)
            self.condition.notify_all()
        if upstream is None:
            return
        for sock in (client, upstream):
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            sock.close()


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
