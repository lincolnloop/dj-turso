"""Helpers shared by dj-turso's own test suites."""

import contextlib
import socket
import threading

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
        self.open_sockets: dict[socket.socket, socket.socket] = {}
        self.drop_next_request = False
        self.answer_bodies: list[bytes] = []
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
    # status 200, in place of the server's answer; then its connection closes.
    def answer_next_requests(self, *bodies: bytes) -> None:
        with self.condition:
            self.answer_bodies.extend(bodies)

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
                    dropping, self.drop_next_request = self.drop_next_request, False
                    answer = self.answer_bodies.pop(0) if self.answer_bodies else None
                if dropping:
                    break
                if answer is not None:
                    client.sendall(
                        b"HTTP/1.1 200 OK\r\nConnection: close\r\n"
                        b"Content-Length: %d\r\n\r\n%s" % (len(answer), answer)
                    )
                    break
                upstream.sendall(request)
        self.close_pair(client)

    def forward_responses(self, client: socket.socket, upstream: socket.socket) -> None:
        with contextlib.suppress(OSError):
            for data in iter(lambda: upstream.recv(65536), b""):
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
    fields = dict(line.partition(b": ")[::2] for line in head.split(b"\r\n")[1:])
    return int(fields[b"Content-Length"])
