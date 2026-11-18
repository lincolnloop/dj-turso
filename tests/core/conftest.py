import collections.abc
import fcntl
import os
import pathlib
import typing as t
import urllib.parse

import django.db
import django.db.utils
import pytest
import pytest_django
import pytest_httpserver
import pytest_socket

from dj_turso import base, session
from tests import utils

pytest_plugins = ["pytester"]

STUB_STREAM_IDLE_TIMEOUT = 3

LOOPBACK_HOST = "127.0.0.1"

allowed_hosts_key = pytest.StashKey[set[str]]()


# Per-worker database files: two processes cannot open one Turso file.
@pytest.fixture(scope="session")
def django_db_modify_db_settings(tmp_path_factory: pytest.TempPathFactory) -> None:
    directory = tmp_path_factory.mktemp("databases")
    # In place: a test may have configured the connection already.
    settings_dict = django.db.connections["default"].settings_dict
    settings_dict["NAME"] = str(directory / "default.db")
    settings_dict["TEST"]["NAME"] = str(directory / "test_default.db")


# Django checks an alias's database once per process, on its first connection:
# checked here, so that no test's requests depend on which test connected first.
@pytest.fixture(autouse=True, scope="session")
def check_default_alias(
    django_db_blocker: pytest_django.DjangoDbBlocker,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    directory = tmp_path_factory.mktemp("check")
    handler = django.db.utils.ConnectionHandler(
        {"default": {"ENGINE": "dj_turso", "NAME": str(directory / "default.db")}}
    )
    connection = handler["default"]
    with django_db_blocker.unblock():
        connection.ensure_connection()
        connection.close()


@pytest.fixture(params=[pytest.param("pyturso", id="pyturso")])
def target(
    django_db_blocker: pytest_django.DjangoDbBlocker, tmp_path: pathlib.Path
) -> collections.abc.Iterator[base.DatabaseWrapper]:
    handler = django.db.utils.ConnectionHandler(
        {"default": {"ENGINE": "dj_turso", "NAME": str(tmp_path / "default.db")}}
    )
    connection = t.cast("base.DatabaseWrapper", handler["default"])
    with django_db_blocker.unblock():
        yield connection
        connection.close()


# A target on each driver, named by its alias.
@pytest.fixture(
    params=[
        pytest.param("pyturso", id="pyturso"),
        pytest.param("stub", id="stub"),
        pytest.param("cloud", id="cloud"),
    ]
)
def driver_target(
    django_db_blocker: pytest_django.DjangoDbBlocker,
    request: pytest.FixtureRequest,
    tmp_path: pathlib.Path,
) -> collections.abc.Iterator[base.DatabaseWrapper]:
    if request.param == "pyturso":
        settings_dict = {"ENGINE": "dj_turso", "NAME": str(tmp_path / "default.db")}
    else:
        settings_dict = get_remote_settings(request)
    handler = django.db.utils.ConnectionHandler({"default": settings_dict})
    connection = base.DatabaseWrapper(handler.settings["default"], request.param)
    with django_db_blocker.unblock():
        yield connection
        connection.close()


# Turso Cloud's database is shared between xdist workers: a test that writes the
# item table holds it alone, and the table is dropped around the test.
@pytest.fixture
def item_table(
    driver_target: base.DatabaseWrapper, tmp_path_factory: pytest.TempPathFactory
) -> collections.abc.Iterator[None]:
    if driver_target.alias != "cloud":
        yield
        return
    # Every worker's base temporary directory shares this parent.
    lock_path = tmp_path_factory.getbasetemp().parent / f"{driver_target.alias}.lock"
    with lock_path.open("w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        drop_item_table(driver_target)
        yield
        driver_target.close()
        drop_item_table(driver_target)


# Threaded, so that it speaks HTTP/1.1 and keeps connections open as Turso Cloud does.
@pytest.fixture(scope="session")
def make_httpserver(
    request: pytest.FixtureRequest,
) -> collections.abc.Iterator[pytest_httpserver.HTTPServer]:
    allow_hosts(request, LOOPBACK_HOST)
    server = pytest_httpserver.HTTPServer(host=LOOPBACK_HOST, port=0, threaded=True)
    server.start()
    yield server
    server.clear()
    server.stop()


# The server outlives the test that started it; each test using it reaches it anew.
@pytest.fixture
def httpserver(
    loopback_network: None, make_httpserver: pytest_httpserver.HTTPServer
) -> pytest_httpserver.HTTPServer:
    make_httpserver.clear()
    return make_httpserver


# The suite runs with sockets disabled; a test connects only to the hosts its
# fixtures allow.
@pytest.fixture
def loopback_network(request: pytest.FixtureRequest) -> None:
    allow_hosts(request, LOOPBACK_HOST)


@pytest.fixture
def cloud_settings(request: pytest.FixtureRequest) -> dict[str, t.Any]:
    url = os.environ.get("TURSO_DATABASE_URL", "")
    auth_token = os.environ.get("TURSO_AUTH_TOKEN", "")
    if not (url and auth_token):
        pytest.skip("needs TURSO_DATABASE_URL and TURSO_AUTH_TOKEN")
    allow_hosts(request, str(urllib.parse.urlsplit(url).hostname))
    return {"ENGINE": "dj_turso", "NAME": url, "OPTIONS": {"auth_token": auth_token}}


# Turso Cloud's HTTP API on a database of the test's own. Its streams idle out sooner
# than Turso Cloud's, and the client gives them up as much sooner, so idle tests wait
# seconds.
@pytest.fixture
def hrana_stub(
    httpserver: pytest_httpserver.HTTPServer,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> collections.abc.Iterator[utils.HranaStub]:
    monkeypatch.setattr(session, "STREAM_IDLE_TIMEOUT", STUB_STREAM_IDLE_TIMEOUT)
    stub = utils.HranaStub(httpserver, tmp_path / "remote.db", STUB_STREAM_IDLE_TIMEOUT)
    yield stub
    stub.close()
    httpserver.check_handler_errors()


@pytest.fixture(
    params=[pytest.param("stub", id="stub"), pytest.param("cloud", id="cloud")]
)
def remote_settings(request: pytest.FixtureRequest) -> dict[str, t.Any]:
    return get_remote_settings(request)


@pytest.fixture
def remote_target(
    django_db_blocker: pytest_django.DjangoDbBlocker, remote_settings: dict[str, t.Any]
) -> collections.abc.Iterator[base.DatabaseWrapper]:
    handler = django.db.utils.ConnectionHandler({"default": remote_settings})
    connection = t.cast("base.DatabaseWrapper", handler["default"])
    with django_db_blocker.unblock():
        yield connection
        connection.close()


@pytest.fixture
def stub_target(
    django_db_blocker: pytest_django.DjangoDbBlocker, hrana_stub: utils.HranaStub
) -> collections.abc.Iterator[base.DatabaseWrapper]:
    handler = django.db.utils.ConnectionHandler(
        {
            "default": {
                "ENGINE": "dj_turso",
                "NAME": hrana_stub.url,
                "OPTIONS": {"auth_token": hrana_stub.auth_token},
            }
        }
    )
    connection = t.cast("base.DatabaseWrapper", handler["default"])
    with django_db_blocker.unblock():
        yield connection
        connection.close()


@pytest.fixture
def proxy(hrana_stub: utils.HranaStub) -> collections.abc.Iterator[utils.CountingProxy]:
    parts = urllib.parse.urlsplit(hrana_stub.url)
    proxy = utils.CountingProxy(str(parts.hostname), int(str(parts.port)))
    yield proxy
    proxy.close()


@pytest.fixture
def proxied_target(
    django_db_blocker: pytest_django.DjangoDbBlocker,
    hrana_stub: utils.HranaStub,
    proxy: utils.CountingProxy,
) -> collections.abc.Iterator[base.DatabaseWrapper]:
    handler = django.db.utils.ConnectionHandler(
        {
            "default": {
                "ENGINE": "dj_turso",
                "NAME": f"http://127.0.0.1:{proxy.port}",
                "OPTIONS": {"auth_token": hrana_stub.auth_token},
            }
        }
    )
    connection = t.cast("base.DatabaseWrapper", handler["default"])
    with django_db_blocker.unblock():
        yield connection
        connection.close()


@pytest.fixture
def tunnel_proxy(loopback_network: None) -> collections.abc.Iterator[utils.TunnelProxy]:
    proxy = utils.TunnelProxy()
    yield proxy
    proxy.close()


# ---


def get_remote_settings(request: pytest.FixtureRequest) -> dict[str, t.Any]:
    if request.param == "cloud":
        return t.cast("dict[str, t.Any]", request.getfixturevalue("cloud_settings"))
    stub = request.getfixturevalue("hrana_stub")
    return {
        "ENGINE": "dj_turso",
        "NAME": stub.url,
        "OPTIONS": {"auth_token": stub.auth_token},
    }


def allow_hosts(request: pytest.FixtureRequest, host: str) -> None:
    allowed_hosts = request.node.stash.setdefault(allowed_hosts_key, set())
    allowed_hosts.add(host)
    pytest_socket.enable_socket()
    pytest_socket.socket_allow_hosts(sorted(allowed_hosts))


def drop_item_table(connection: base.DatabaseWrapper) -> None:
    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS item")
