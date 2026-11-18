import secrets
import time

import pytest
import pytest_httpserver

from tests import utils

API_TOKEN = secrets.token_urlsafe()

DATABASES_PATH = "/v1/organizations/my-org/databases"


def test_sweep_deletes_stale_test_databases_only(
    httpserver: pytest_httpserver.HTTPServer,
) -> None:
    stale_at = int(time.time()) - utils.STALE_TEST_DATABASE_AGE - 60
    fresh_at = int(time.time()) - 60
    expect_listing(
        httpserver,
        [
            f"djturso-test-{stale_at}-0a1b2c3d",
            f"djturso-test-{fresh_at}-4e5f6a7b",
            "my-app",
            "djturso-test-yesterday-0a1b2c3d",
            f"djturso-test-{stale_at}-0a1b2c3d-copy",
            f"my-djturso-test-{stale_at}-0a1b2c3d",
            f"djturso-test-{stale_at}-8c9d0e1f",
        ],
    )
    for name in (
        f"djturso-test-{stale_at}-0a1b2c3d",
        f"djturso-test-{stale_at}-8c9d0e1f",
    ):
        expect_deletion(httpserver, name).respond_with_json({"database": name})
    client = make_client(httpserver)

    client.delete_stale_test_databases("dj-turso-tests")

    assert get_received_requests(httpserver) == [
        f"GET {DATABASES_PATH}?group=dj-turso-tests",
        f"DELETE {DATABASES_PATH}/djturso-test-{stale_at}-0a1b2c3d",
        f"DELETE {DATABASES_PATH}/djturso-test-{stale_at}-8c9d0e1f",
    ]


def test_sweep_skips_database_another_process_deleted(
    httpserver: pytest_httpserver.HTTPServer,
) -> None:
    stale_at = int(time.time()) - utils.STALE_TEST_DATABASE_AGE - 60
    gone = f"djturso-test-{stale_at}-0a1b2c3d"
    remaining = f"djturso-test-{stale_at}-8c9d0e1f"
    expect_listing(httpserver, [gone, remaining])
    expect_deletion(httpserver, gone).respond_with_json(
        {"error": f"could not find database with name {gone}: record not found"},
        status=404,
    )
    expect_deletion(httpserver, remaining).respond_with_json({"database": remaining})
    client = make_client(httpserver)

    client.delete_stale_test_databases("dj-turso-tests")

    assert get_received_requests(httpserver) == [
        f"GET {DATABASES_PATH}?group=dj-turso-tests",
        f"DELETE {DATABASES_PATH}/{gone}",
        f"DELETE {DATABASES_PATH}/{remaining}",
    ]


def test_refused_request_raises_with_answer(
    httpserver: pytest_httpserver.HTTPServer,
) -> None:
    httpserver.expect_request(DATABASES_PATH, method="GET").respond_with_data(
        '{"error":"token contains an invalid number of segments"}',
        status=401,
        content_type="application/json",
    )
    client = make_client(httpserver)

    with pytest.raises(utils.PlatformRequestError) as exc_info:
        client.delete_stale_test_databases("dj-turso-tests")

    assert str(exc_info.value) == (
        f"GET {DATABASES_PATH}?group=dj-turso-tests answered HTTP status 401: "
        '{"error":"token contains an invalid number of segments"}'
    )


def test_sweep_raises_on_refused_deletion(
    httpserver: pytest_httpserver.HTTPServer,
) -> None:
    stale_at = int(time.time()) - utils.STALE_TEST_DATABASE_AGE - 60
    name = f"djturso-test-{stale_at}-0a1b2c3d"
    expect_listing(httpserver, [name])
    expect_deletion(httpserver, name).respond_with_data(
        '{"error":"token contains an invalid number of segments"}',
        status=401,
        content_type="application/json",
    )
    client = make_client(httpserver)

    with pytest.raises(utils.PlatformRequestError) as exc_info:
        client.delete_stale_test_databases("dj-turso-tests")

    assert str(exc_info.value) == (
        f"DELETE {DATABASES_PATH}/{name} answered HTTP status 401: "
        '{"error":"token contains an invalid number of segments"}'
    )


# ---


def make_client(httpserver: pytest_httpserver.HTTPServer) -> utils.PlatformClient:
    return utils.PlatformClient(
        API_TOKEN, "my-org", base_url=httpserver.url_for("").rstrip("/")
    )


# The Platform API's listing of a group's databases, as it describes each.
def expect_listing(httpserver: pytest_httpserver.HTTPServer, names: list[str]) -> None:
    httpserver.expect_request(
        DATABASES_PATH,
        method="GET",
        query_string={"group": "dj-turso-tests"},
        headers={"Authorization": f"Bearer {API_TOKEN}"},
    ).respond_with_json(
        {
            "databases": [
                {
                    "Name": name,
                    "DbId": f"0eb771dd-6906-11ee-8553-eaa7715aea{index:02x}",
                    "Hostname": f"{name}-my-org.aws-us-east-1.turso.io",
                    "block_reads": False,
                    "block_writes": False,
                    "regions": ["aws-us-east-1"],
                    "primaryRegion": "aws-us-east-1",
                    "group": "dj-turso-tests",
                    "delete_protection": False,
                    "parent": None,
                }
                for index, name in enumerate(names)
            ]
        }
    )


def expect_deletion(
    httpserver: pytest_httpserver.HTTPServer, name: str
) -> pytest_httpserver.RequestHandler:
    return httpserver.expect_request(
        f"{DATABASES_PATH}/{name}",
        method="DELETE",
        headers={"Authorization": f"Bearer {API_TOKEN}"},
    )


# Each request the server received, as its method and request target.
def get_received_requests(httpserver: pytest_httpserver.HTTPServer) -> list[str]:
    return [
        f"{request.method} {request.environ['RAW_URI']}"
        for request, _ in httpserver.log
    ]
