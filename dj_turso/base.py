import typing as t

from django.db.backends.sqlite3 import base as sqlite_base
from django.utils import asyncio

from dj_turso import creation, cursor, drivers, features, introspection, operations

# Options Django's sqlite3 backend sets for the stdlib module that no Turso
# driver accepts.
STDLIB_CONNECT_KWARGS = ("check_same_thread", "detect_types", "uri")

# The SQLite release Turso's dialect tracks. Constant, not the driver's report:
# remote drivers report none, and Turso lags SQLite where a version would claim.
SQLITE_DIALECT_VERSION = (3, 50, 4)


class DatabaseWrapper(sqlite_base.DatabaseWrapper):
    display_name = "Turso"
    init_commands: list[str]
    creation_class = creation.DatabaseCreation
    features_class = features.DatabaseFeatures
    introspection_class = introspection.DatabaseIntrospection
    ops_class = operations.DatabaseOperations

    def __init__(self, settings_dict: dict[str, t.Any], alias: str = "default") -> None:
        super().__init__(settings_dict, alias)
        self.Database = drivers.get_database_module(str(settings_dict["NAME"]))
        # Test database setup swaps NAME in place; keep what was configured.
        self.configured_name_is_remote = drivers.is_remote_name(
            str(settings_dict["NAME"])
        )

    def get_connection_params(self) -> dict[str, t.Any]:
        kwargs = super().get_connection_params()
        for name in STDLIB_CONNECT_KWARGS:
            del kwargs[name]
        database = str(kwargs["database"])
        # A remote database's token has no use on its local test database.
        if self.configured_name_is_remote and not drivers.is_remote_name(database):
            kwargs.pop("auth_token", None)
        return kwargs | {"database": database, "isolation_level": None}

    def get_database_version(self) -> tuple[int, ...]:
        return SQLITE_DIALECT_VERSION

    @asyncio.async_unsafe
    def get_new_connection(self, conn_params: dict[str, t.Any]) -> t.Any:
        # Test database setup swaps NAME after init, possibly from a URL to a file.
        self.Database = drivers.get_database_module(conn_params["database"])
        with drivers.relabel_engine_errors():
            connection = self.Database.connect(**conn_params)
        connection.execute("PRAGMA foreign_keys = ON")
        for init_command in self.init_commands:
            if init_command := init_command.strip():
                connection.execute(init_command)
        return connection

    def is_usable(self) -> bool:
        usable: bool = self.Database.is_usable(self.connection)
        return usable

    def create_cursor(self, name: str | None = None) -> t.Any:
        return cursor.TursoCursorWrapper(self.connection.cursor())
