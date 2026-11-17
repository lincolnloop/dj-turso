import django.db
import pytest
from django.db.models import aggregates, expressions
from django.test import utils as test_utils

from tests.core import models


@pytest.mark.django_db
def test_bulk_create_beyond_query_parameter_limit_inserts_every_row() -> None:
    # One past SQLite's default variable limit.
    row_count = 32_767

    models.Tag.objects.bulk_create(
        models.Tag(name=name) for name in map(str, range(row_count))
    )

    assert models.Tag.objects.count() == row_count


@pytest.mark.django_db
def test_captured_query_shows_quoted_parameters() -> None:
    with test_utils.CaptureQueriesContext(django.db.connection) as context:
        list(models.Tag.objects.filter(name="it's"))

    assert [query["sql"] for query in context.captured_queries] == [
        (
            'SELECT "core_tag"."id", "core_tag"."name" FROM "core_tag" '
            "WHERE \"core_tag\".\"name\" = 'it''s'"
        )
    ]


@pytest.mark.django_db
def test_ordered_aggregate_raises_not_supported_error() -> None:
    models.Tag.objects.create(name="a")

    with pytest.raises(django.db.NotSupportedError) as excinfo:
        models.Tag.objects.aggregate(
            names=aggregates.StringAgg("name", expressions.Value(","), order_by="-name")
        )

    assert str(excinfo.value) == (
        "This database backend does not support specifying an order on aggregates."
    )
