import django.db
import pytest


@pytest.mark.django_db
def test_table_names_leave_out_turso_internal_tables() -> None:
    with django.db.connection.cursor() as cursor:
        table_names = django.db.connection.introspection.table_names(cursor)

    assert table_names == [
        "core_attachment",
        "core_counter",
        "core_event",
        "core_product",
        "core_tag",
    ]


# SQLite matches table names without regard to case.
@pytest.mark.django_db
@pytest.mark.parametrize(
    "table_name",
    [
        pytest.param("core_product", id="declared"),
        pytest.param("CORE_PRODUCT", id="other-case"),
    ],
)
def test_table_description_keeps_declared_varchar_length(table_name: str) -> None:
    with django.db.connection.cursor() as cursor:
        description = django.db.connection.introspection.get_table_description(
            cursor, table_name
        )

    assert [info._asdict() for info in description] == [
        {
            "name": "id",
            "type_code": "integer",
            "display_size": None,
            "internal_size": None,
            "precision": None,
            "scale": None,
            "null_ok": False,
            "default": None,
            "collation": None,
            "pk": True,
            "has_json_constraint": False,
        },
        {
            "name": "name",
            "type_code": "varchar(30)",
            "display_size": 30,
            "internal_size": None,
            "precision": None,
            "scale": None,
            "null_ok": False,
            "default": None,
            "collation": None,
            "pk": False,
            "has_json_constraint": False,
        },
    ]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "table_name",
    [
        pytest.param("core_product", id="declared"),
        pytest.param("CORE_PRODUCT", id="other-case"),
    ],
)
def test_constraints_keep_declared_index_name(table_name: str) -> None:
    with django.db.connection.cursor() as cursor:
        constraints = django.db.connection.introspection.get_constraints(
            cursor, table_name
        )

    assert constraints == {
        "ProductNameIndex": {
            "columns": ["name"],
            "primary_key": False,
            "unique": False,
            "foreign_key": None,
            "check": False,
            "index": True,
            "type": "idx",
            "orders": ["ASC"],
        },
        "__primary__": {
            "columns": ["id"],
            "primary_key": True,
            "unique": False,
            "foreign_key": None,
            "check": False,
            "index": False,
        },
    }
