"""The PEP 249 driver behind a connection, chosen from the database NAME."""

import collections.abc
import contextlib
import types
import typing as t

import turso
from turso import _turso


class DatabaseModule(types.ModuleType):
    """What Django's backend reads off its ``Database`` module, for one driver."""

    connect: collections.abc.Callable[..., t.Any]
    # Stdlib sqlite3's Binary: the drivers have none, and the cursor binds it as bytes.
    Binary = memoryview
    Error: type[Exception]
    InterfaceError: type[Exception]
    DatabaseError: type[Exception]
    DataError: type[Exception]
    OperationalError: type[Exception]
    IntegrityError: type[Exception]
    InternalError: type[Exception]
    ProgrammingError: type[Exception]
    NotSupportedError: type[Exception]

    def __init__(self, driver: types.ModuleType) -> None:
        super().__init__(driver.__name__)
        self.connect = driver.connect
        self.Error = driver.Error
        self.InterfaceError = driver.InterfaceError
        self.DatabaseError = driver.DatabaseError
        self.DataError = driver.DataError
        self.OperationalError = driver.OperationalError
        self.IntegrityError = driver.IntegrityError
        self.InternalError = driver.InternalError
        self.ProgrammingError = driver.ProgrammingError
        self.NotSupportedError = driver.NotSupportedError


def get_database_module(name: str) -> DatabaseModule:
    return DatabaseModule(turso)


# pyturso raises SQLite's generic error code, stdlib sqlite3's OperationalError, as
# bare DatabaseError; only the engine's own class, left as context, tells it apart.
@contextlib.contextmanager
def relabel_engine_errors() -> collections.abc.Iterator[None]:
    try:
        yield
    except turso.DatabaseError as exc:
        if type(exc) is turso.DatabaseError and type(exc.__context__) is _turso.Error:
            raise turso.OperationalError(*exc.args) from exc
        raise
