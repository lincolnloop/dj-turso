import django.db
import pytest


@pytest.mark.django_db
def test_table_names_leave_out_turso_internal_tables() -> None:
    with django.db.connection.cursor() as cursor:
        table_names = django.db.connection.introspection.table_names(cursor)

    assert table_names == ["core_attachment", "core_event", "core_tag"]
