import django.db
import pytest

from dj_turso import base


@pytest.mark.parametrize(
    "table_names",
    [
        pytest.param(None, id="all-tables"),
        pytest.param(["child", "ledger", "orphan"], id="named-tables"),
    ],
)
def test_check_constraints_raises_integrity_error_for_dangling_foreign_key(
    table_names: list[str] | None, target: base.DatabaseWrapper
) -> None:
    with target.cursor() as cursor:
        cursor.execute('CREATE TABLE "parent" ("id" integer PRIMARY KEY)')
        for table_name in ("child", "orphan"):
            cursor.execute(
                f'CREATE TABLE "{table_name}" ("id" integer PRIMARY KEY, '
                '"parent_id" integer NULL REFERENCES "parent" ("id"))'
            )
        cursor.execute(
            'CREATE TABLE "ledger" ("parent_id" integer REFERENCES "parent" ("id"))'
        )
        cursor.execute('INSERT INTO "parent" VALUES (1)')
        cursor.execute('INSERT INTO "child" VALUES (1, 1)')
        cursor.execute('INSERT INTO "ledger" VALUES (1)')
    with target.constraint_checks_disabled(), target.cursor() as cursor:
        cursor.execute('INSERT INTO "orphan" VALUES (2, 99)')

    with pytest.raises(django.db.IntegrityError) as excinfo:
        target.check_constraints(table_names)

    assert str(excinfo.value) == (
        "The row in table 'orphan' with primary key '2' has an invalid foreign key: "
        "orphan.parent_id contains a value '99' that does not have a corresponding "
        "value in parent.id."
    )


def test_check_constraints_names_rowid_of_table_without_primary_key(
    target: base.DatabaseWrapper,
) -> None:
    with target.cursor() as cursor:
        cursor.execute('CREATE TABLE "parent" ("id" integer PRIMARY KEY)')
        cursor.execute(
            'CREATE TABLE "ledger" ("parent_id" integer REFERENCES "parent" ("id"))'
        )
    with target.constraint_checks_disabled(), target.cursor() as cursor:
        cursor.execute('INSERT INTO "ledger" VALUES (99)')

    with pytest.raises(django.db.IntegrityError) as excinfo:
        target.check_constraints()

    assert str(excinfo.value) == (
        "The row in table 'ledger' with primary key '1' has an invalid foreign key: "
        "ledger.parent_id contains a value '99' that does not have a corresponding "
        "value in parent.id."
    )
