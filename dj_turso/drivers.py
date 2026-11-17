"""The PEP 249 driver behind a connection, chosen from the database NAME."""

import collections.abc
import contextlib
import types
import typing as t
import urllib.parse

import turso
from turso import _turso

# Remote names need the serverless extra; without it, local names still work.
try:
    import turso_serverless

    from dj_turso import session
except ImportError as exc:
    serverless_import_error: ImportError | None = exc
else:
    serverless_import_error = None

# NAME schemes served over HTTP by turso_serverless; anything else is a local file.
REMOTE_SCHEMES = frozenset({"http", "https", "libsql", "turso"})

MISSING_SERVERLESS_EXTRA = (
    "A database NAME that is a URL needs the serverless extra: "
    "install dj-turso[serverless]."
)


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

    def __init__(
        self,
        driver: types.ModuleType,
        connect: collections.abc.Callable[..., t.Any],
    ) -> None:
        super().__init__(driver.__name__)
        self.connect = connect
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
    if urllib.parse.urlsplit(name).scheme in REMOTE_SCHEMES:
        if serverless_import_error is not None:
            raise ImportError(MISSING_SERVERLESS_EXTRA) from serverless_import_error
        return DatabaseModule(turso_serverless, session.connect)
    return DatabaseModule(turso, turso.connect)


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
