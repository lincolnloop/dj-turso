import typing as t

from django.db.backends.base import introspection as base_introspection
from django.db.backends.sqlite3 import introspection as sqlite_introspection


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
