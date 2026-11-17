import re
import typing as t

from django.db.backends.base import introspection as base_introspection
from django.db.backends.sqlite3 import introspection as sqlite_introspection

index_name_re = re.compile(
    r'^CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?("[^"]+"|\S+)',
    re.IGNORECASE,
)


class DatabaseIntrospection(sqlite_introspection.DatabaseIntrospection):
    # Turso keeps each AUTOINCREMENT counter in a table of its own, listed in
    # sqlite_master beside sqlite_sequence but not droppable.
    def get_table_list(self, cursor: t.Any) -> list[base_introspection.TableInfo]:
        cursor.execute("""
            SELECT name, type FROM sqlite_master
            WHERE type in ('table', 'view') AND NOT name='sqlite_sequence'
            AND name NOT LIKE '\\_\\_turso\\_internal\\_%' ESCAPE '\\'
            ORDER BY name""")
        return [
            base_introspection.TableInfo(row[0], row[1][0]) for row in cursor.fetchall()
        ]

    # Turso's PRAGMA table_xinfo drops the arguments of a declared type, such as
    # varchar's length; the schema SQL in sqlite_master keeps them.
    def get_table_description(
        self, cursor: t.Any, table_name: str
    ) -> list[sqlite_introspection.FieldInfo]:
        # django-stubs types type_code as an int; sqlite3 reports the declared type.
        description: list[t.Any] = super().get_table_description(cursor, table_name)
        cursor.execute(
            "SELECT sql FROM sqlite_master WHERE name = %s COLLATE NOCASE", [table_name]
        )
        (schema_sql,) = cursor.fetchone()
        return [get_declared_field_info(info, schema_sql) for info in description]

    # Turso lowercases index names in sqlite_master and its index pragmas; the
    # CREATE INDEX statement keeps the name as declared.
    def get_constraints(self, cursor: t.Any, table_name: str) -> dict[str, t.Any]:
        constraints = super().get_constraints(cursor, table_name)
        cursor.execute(
            """
            SELECT name, sql FROM sqlite_master
            WHERE type = 'index' AND tbl_name = %s COLLATE NOCASE
            AND sql IS NOT NULL""",
            [table_name],
        )
        for stored_name, index_sql in cursor.fetchall():
            constraints[get_declared_index_name(index_sql)] = constraints.pop(
                stored_name
            )
        return constraints


def get_declared_field_info(info: t.Any, schema_sql: str) -> t.Any:
    match = re.search(
        rf'[(,]\s*"?{re.escape(info.name)}"?\s+{re.escape(info.type_code)}'
        r"\s*(\([^()]*\))",
        schema_sql,
        re.IGNORECASE,
    )
    if match is None:
        return info
    type_code = f"{info.type_code}{match[1]}"
    return info._replace(
        type_code=type_code, display_size=sqlite_introspection.get_field_size(type_code)
    )


def get_declared_index_name(index_sql: str) -> str:
    name: str = index_name_re.findall(index_sql)[0]
    return name.strip('"')
