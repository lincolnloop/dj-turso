import collections.abc
import http.client
import os
import pathlib
import typing as t

import django.db
import django.db.utils
import pytest
import pytest_django

from dj_turso import base

# Signed by the test-only key whose public half compose.yaml gives the sqld service.
SQLD_JWT = (
    "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicncifQ."
    "n8r4qqfg1S8pfrOIl_C7EMdnotATmuPCsVYz-lXO9JUDcrEOYUVM-AadvNwy8GVE9gmT3g8DL_bfKk6QevR1CQ"
)


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
    if request.param == "sqld":
        url = request.getfixturevalue("sqld_url")
        auth_token = SQLD_JWT
    else:
        url = os.environ.get("TURSO_DATABASE_URL", "")
        auth_token = os.environ.get("TURSO_AUTH_TOKEN", "")
        if not (url and auth_token):
            pytest.skip("needs TURSO_DATABASE_URL and TURSO_AUTH_TOKEN")
    handler = django.db.utils.ConnectionHandler(
        {
            "default": {
                "ENGINE": "dj_turso",
                "NAME": url,
                "OPTIONS": {"auth_token": auth_token},
            }
        }
    )
    connection = t.cast("base.DatabaseWrapper", handler["default"])
    with django_db_blocker.unblock():
        yield connection
        connection.close()
