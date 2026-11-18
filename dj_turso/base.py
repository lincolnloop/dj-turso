import collections.abc
import typing as t

from django.db import utils as db_utils
from django.db.backends.sqlite3 import base as sqlite_base
from django.utils import asyncio, functional

from dj_turso import (
    creation,
    cursor,
    drivers,
    exceptions,
    features,
    introspection,
    operations,
)

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
    sql_dangling_foreign_keys = (
        "SELECT referring.%(primary_key)s, referring.%(column)s "
        "FROM %(table)s AS referring LEFT JOIN %(referenced_table)s AS referred "
        "ON referring.%(column)s = referred.%(referenced_column)s "
        "WHERE referring.%(column)s IS NOT NULL "
        "AND referred.%(referenced_column)s IS NULL"
    )

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

    # Only whether turso_version() exists tells Turso from libSQL: its value differs
    # between the local driver and Turso Cloud, and so orders nothing.
    def check_database_version_supported(self) -> None:
        super().check_database_version_supported()
        self.get_turso_version()

    def get_turso_version(self) -> str:
        try:
            return self.turso_version
        except db_utils.OperationalError as exc:
            if not str(exc).endswith("no such function: turso_version"):
                raise
            raise exceptions.EngineNotSupportedError from exc

    @functional.cached_property
    def turso_version(self) -> str:
        with self.temporary_connection() as cursor:
            cursor.execute("SELECT turso_version()")
            row = cursor.fetchone()
        return str(row[0])

    @asyncio.async_unsafe
    def get_new_connection(self, conn_params: dict[str, t.Any]) -> t.Any:
        # Test database setup swaps NAME after init, possibly from a URL to a file.
        self.Database = drivers.get_database_module(conn_params["database"])
        with drivers.relabel_engine_errors():
            connection = self.Database.connect(**conn_params)
        init_commands = filter(None, map(str.strip, self.init_commands))
        self.Database.set_up_session(
            connection, ["PRAGMA foreign_keys = ON", *init_commands]
        )
        return connection

    def check_constraints(
        self, table_names: collections.abc.Iterable[str] | None = None
    ) -> None:
        # Turso answers PRAGMA foreign_key_check with no rows, so join each
        # foreign key to its target the way Django's MySQL backend does. Like
        # the pragma, a table without a primary key reports its rowid.
        quote_name = self.ops.quote_name
        with self.cursor() as cursor:
            if table_names is None:
                table_names = self.introspection.table_names(cursor)
            for table_name in table_names:
                primary_key_column_name = (
                    self.introspection.get_primary_key_column(cursor, table_name)
                    or "rowid"
                )
                relations = self.introspection.get_relations(cursor, table_name)
                for column_name, relation in relations.items():
                    referenced_column_name, referenced_table_name = relation[:2]
                    cursor.execute(
                        self.sql_dangling_foreign_keys
                        % {
                            "primary_key": quote_name(primary_key_column_name),
                            "column": quote_name(column_name),
                            "table": quote_name(table_name),
                            "referenced_table": quote_name(referenced_table_name),
                            "referenced_column": quote_name(referenced_column_name),
                        }
                    )
                    bad_row = cursor.fetchone()
                    if bad_row is not None:
                        primary_key_value, bad_value = bad_row
                        message = (
                            f"The row in table '{table_name}' with primary key "
                            f"'{primary_key_value}' has an invalid foreign key: "
                            f"{table_name}.{column_name} contains a value "
                            f"'{bad_value}' that does not have a corresponding "
                            f"value in {referenced_table_name}."
                            f"{referenced_column_name}."
                        )
                        raise db_utils.IntegrityError(message)

    def is_usable(self) -> bool:
        usable: bool = self.Database.is_usable(self.connection)
        return usable

    def create_cursor(self, name: str | None = None) -> t.Any:
        return cursor.TursoCursorWrapper(self.connection.cursor())
