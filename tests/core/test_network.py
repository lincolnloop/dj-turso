import re
import socket

import pytest
import pytest_socket

BLOCKED_MESSAGE = "A test tried to use socket.getaddrinfo."


def test_test_without_network_fixture_cannot_connect() -> None:
    with (
        pytest.raises(pytest_socket.SocketBlockedError) as excinfo,
        pytest.warns(UserWarning, match=f"^{re.escape(BLOCKED_MESSAGE)}$"),
    ):
        socket.create_connection(("127.0.0.1", 9))

    assert str(excinfo.value) == BLOCKED_MESSAGE
