"""Cases for Django's own runner on the default test database."""

import concurrent.futures
import http.client

import django.db
import django.http
from django import test, urls

from tests.core import models


class CommittedRowsTests(test.TransactionTestCase):
    def test_thread_reads_committed_rows(self) -> None:
        models.Tag.objects.create(name="committed")

        with concurrent.futures.ThreadPoolExecutor() as executor:
            names = executor.submit(get_tag_names).result()

        assert names == ["committed"]


class TablesTests(test.TestCase):
    def test_thread_reads_migrated_tables(self) -> None:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            table_names = executor.submit(get_table_names).result()

        assert table_names == [
            "core_attachment",
            "core_counter",
            "core_event",
            "core_product",
            "core_shift",
            "core_slug",
            "core_tag",
        ]


# Without STATIC_URL, the live server serves every path as a static file.
@test.override_settings(ROOT_URLCONF="tests.core.thread_cases", STATIC_URL="static/")
class LiveServerTests(test.LiveServerTestCase):
    def test_server_reads_committed_rows(self) -> None:
        models.Tag.objects.create(name="committed")

        connection = http.client.HTTPConnection(self.host, self.server_thread.port)
        connection.request("GET", "/tags/")
        body = connection.getresponse().read().decode()
        connection.close()

        assert body == "committed"


def list_tags(_request: django.http.HttpRequest) -> django.http.HttpResponse:
    return django.http.HttpResponse(
        "\n".join(models.Tag.objects.values_list("name", flat=True))
    )


urlpatterns = [urls.path("tags/", list_tags)]


# ---


def get_tag_names() -> list[str]:
    names = list(models.Tag.objects.values_list("name", flat=True))
    django.db.connection.close()
    return names


def get_table_names() -> list[str]:
    table_names: list[str] = django.db.connection.introspection.table_names()
    django.db.connection.close()
    return table_names
