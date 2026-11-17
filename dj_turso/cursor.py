"""The cursor Django's sqlite3 backend expects, over a Turso driver cursor."""

import collections.abc
import typing as t

from dj_turso import drivers


class TursoCursorWrapper:
    def __init__(self, cursor: t.Any) -> None:
        self.cursor = cursor

    def __getattr__(self, name: str) -> t.Any:
        return getattr(self.cursor, name)

    def __iter__(self) -> collections.abc.Iterator[t.Any]:
        with drivers.relabel_engine_errors():
            yield from self.cursor

    def execute(self, query: str, params: t.Any = None) -> t.Self:
        with drivers.relabel_engine_errors():
            if params is None:
                self.cursor.execute(query)
            else:
                self.cursor.execute(query, params)
        return self

    def fetchone(self) -> t.Any:
        with drivers.relabel_engine_errors():
            return self.cursor.fetchone()

    def fetchmany(self, size: int | None = None) -> list[t.Any]:
        with drivers.relabel_engine_errors():
            rows: list[t.Any] = self.cursor.fetchmany(size)
        return rows

    def fetchall(self) -> list[t.Any]:
        with drivers.relabel_engine_errors():
            rows: list[t.Any] = self.cursor.fetchall()
        return rows
