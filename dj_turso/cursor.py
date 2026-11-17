"""The cursor Django's sqlite3 backend expects, over a Turso driver cursor."""

import collections.abc
import datetime as dt
import decimal
import itertools
import typing as t
import uuid

from django.db.backends.sqlite3 import base as sqlite_base

from dj_turso import drivers


class TursoCursorWrapper:
    def __init__(self, cursor: t.Any) -> None:
        self.cursor = cursor
        self.ran_no_statements = False

    def __getattr__(self, name: str) -> t.Any:
        return getattr(self.cursor, name)

    def __iter__(self) -> collections.abc.Iterator[t.Any]:
        with drivers.relabel_engine_errors():
            yield from self.cursor

    # The drivers report -1 when executemany() is given no parameters; stdlib sqlite3
    # reports the rows changed, 0.
    @property
    def rowcount(self) -> int:
        if self.ran_no_statements:
            return 0
        rowcount: int = self.cursor.rowcount
        return rowcount

    def execute(self, query: str, params: t.Any = None) -> t.Self:
        self.ran_no_statements = False
        with drivers.relabel_engine_errors():
            if params is None:
                self.cursor.execute(query)
            else:
                self.cursor.execute(convert_query(query, params), adapt_params(params))
        return self

    def executemany(
        self, query: str, param_list: collections.abc.Iterable[t.Any]
    ) -> t.Self:
        peekable, param_list = itertools.tee(param_list)
        first_params = next(peekable, None)
        self.ran_no_statements = first_params is None
        with drivers.relabel_engine_errors():
            self.cursor.executemany(
                convert_query(query, first_params),
                (adapt_params(params) for params in param_list),
            )
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


# Django writes "format" and "pyformat" placeholders; the drivers read "qmark" and
# "named". A literal "%s" needs "%%s" once parameters are passed.
def convert_query(query: str, params: t.Any) -> str:
    if isinstance(params, collections.abc.Mapping):
        return query % {name: f":{name}" for name in params}
    return sqlite_base.FORMAT_QMARK_REGEX.sub("?", query).replace("%%", "%")


def adapt_params(params: t.Any) -> t.Any:
    if isinstance(params, collections.abc.Mapping):
        return {name: adapt_value(value) for name, value in params.items()}
    return tuple(adapt_value(value) for value in params)


# The storage forms Django's sqlite3 backend registers as stdlib adapters, which the
# drivers lack; they bind only None, numbers, strings and bytes.
def adapt_value(value: object) -> object:
    match value:
        case dt.datetime():
            return value.isoformat(" ")
        case dt.date() | dt.time():
            return value.isoformat()
        case decimal.Decimal() | uuid.UUID():
            return str(value)
        case memoryview():
            return bytes(value)
        case _:
            return value
