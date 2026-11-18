import pathlib

import django.db
import pytest
import pytest_django
from django.core import management
from django.core.management import base
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


def test_generated_field_fails_database_system_check(
    monkeypatch: pytest.MonkeyPatch,
    settings: pytest_django.Settings,
    tmp_path: pathlib.Path,
) -> None:
    app_path = tmp_path / "generated_app"
    app_path.mkdir()
    (app_path / "__init__.py").write_text("")
    (app_path / "models.py").write_text(
        "from django.db import models\n"
        "\n"
        "\n"
        "class Square(models.Model):\n"
        "    side = models.IntegerField()\n"
        "    area = models.GeneratedField(\n"
        '        expression=models.F("side") * models.F("side"),\n'
        "        output_field=models.IntegerField(),\n"
        "        db_persist=True,\n"
        "    )\n"
    )
    monkeypatch.syspath_prepend(tmp_path)
    settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "generated_app"]

    with pytest.raises(base.SystemCheckError) as excinfo:
        management.call_command("check", "generated_app", databases=["default"])

    assert str(excinfo.value) == (
        "SystemCheckError: System check identified some issues:\n"
        "\n"
        "ERRORS:\n"
        "generated_app.Square.area: (fields.E220) Turso does not support "
        "GeneratedFields.\n"
        "generated_app.Square.area: (fields.E222) Turso does not support persisted "
        "GeneratedFields.\n"
        "\tHINT: Set db_persist=False on the field.\n"
        "\n"
        "System check identified 2 issues (0 silenced)."
    )
