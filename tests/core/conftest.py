import collections.abc
import fcntl
import http.client
import os
import pathlib
import typing as t
import urllib.parse

import django.db
import django.db.utils
import pytest
import pytest_django

from dj_turso import base
from tests import utils

pytest_plugins = ["pytester"]


# Per-worker database files: two processes cannot open one Turso file.
@pytest.fixture(scope="session")
def django_db_modify_db_settings(tmp_path_factory: pytest.TempPathFactory) -> None:
    directory = tmp_path_factory.mktemp("databases")
    # In place: a test may have configured the connection already.
    settings_dict = django.db.connections["default"].settings_dict
    settings_dict["NAME"] = str(directory / "default.db")
    settings_dict["TEST"]["NAME"] = str(directory / "test_default.db")


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
        pytest.param("sqld", id="sqld"),
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


# A remote target's database is shared between xdist workers: a test that writes the
# item table holds it alone, and the table is dropped around the test.
@pytest.fixture
def item_table(
    driver_target: base.DatabaseWrapper, tmp_path_factory: pytest.TempPathFactory
) -> collections.abc.Iterator[None]:
    if driver_target.alias == "pyturso":
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


# A stand-in for Turso Cloud's HTTP API. Its engine is libSQL, so only the transport is
# tested against it.
@pytest.fixture(scope="session")
def sqld_url() -> str:
    port = int(os.environ.get("DJ_TURSO_SQLD_PORT", "8880"))
    url = f"http://127.0.0.1:{port}"
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", "/health")
        connection.getresponse()
    except OSError as exc:
        error = exc
    else:
        return url
    finally:
        connection.close()
    # Outside the handler: the hint is the whole report, without the traceback.
    pytest.fail(
        f"sqld is not reachable at {url} ({error}): run `docker compose up -d --wait`",
        pytrace=False,
    )


@pytest.fixture(
    params=[pytest.param("sqld", id="sqld"), pytest.param("cloud", id="cloud")]
)
def remote_target(
    django_db_blocker: pytest_django.DjangoDbBlocker, request: pytest.FixtureRequest
) -> collections.abc.Iterator[base.DatabaseWrapper]:
    handler = django.db.utils.ConnectionHandler(
        {"default": get_remote_settings(request)}
    )
    connection = t.cast("base.DatabaseWrapper", handler["default"])
    with django_db_blocker.unblock():
        yield connection
        connection.close()


@pytest.fixture
def proxy(sqld_url: str) -> collections.abc.Iterator[utils.CountingProxy]:
    parts = urllib.parse.urlsplit(sqld_url)
    proxy = utils.CountingProxy(str(parts.hostname), int(str(parts.port)))
    yield proxy
    proxy.close()


@pytest.fixture
def proxied_target(
    django_db_blocker: pytest_django.DjangoDbBlocker, proxy: utils.CountingProxy
) -> collections.abc.Iterator[base.DatabaseWrapper]:
    handler = django.db.utils.ConnectionHandler(
        {
            "default": {
                "ENGINE": "dj_turso",
                "NAME": f"http://127.0.0.1:{proxy.port}",
                "OPTIONS": {"auth_token": utils.SQLD_JWT},
            }
        }
    )
    connection = t.cast("base.DatabaseWrapper", handler["default"])
    with django_db_blocker.unblock():
        yield connection
        connection.close()


@pytest.fixture
def tunnel_proxy() -> collections.abc.Iterator[utils.TunnelProxy]:
    proxy = utils.TunnelProxy()
    yield proxy
    proxy.close()


# ---


def get_remote_settings(request: pytest.FixtureRequest) -> dict[str, t.Any]:
    if request.param == "sqld":
        url = request.getfixturevalue("sqld_url")
        auth_token = utils.SQLD_JWT
    else:
        url = os.environ.get("TURSO_DATABASE_URL", "")
        auth_token = os.environ.get("TURSO_AUTH_TOKEN", "")
        if not (url and auth_token):
            pytest.skip("needs TURSO_DATABASE_URL and TURSO_AUTH_TOKEN")
    return {"ENGINE": "dj_turso", "NAME": url, "OPTIONS": {"auth_token": auth_token}}


def drop_item_table(connection: base.DatabaseWrapper) -> None:
    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS item")
