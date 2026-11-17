import typing as t

from django.db.backends.base import operations as base_operations
from django.db.backends.sqlite3 import operations as sqlite_operations

from dj_turso import cursor, features

# SQLite's compile-time default for SQLITE_MAX_COLUMN, which Turso keeps.
MAX_COLUMNS = 2000


class DatabaseOperations(sqlite_operations.DatabaseOperations):
    # Django's sqlite3 backend gates this on the stdlib's SQLite; Turso's dialect is
    # past the 3.39 fix it checks for.
    force_group_by = base_operations.BaseDatabaseOperations.force_group_by

    # Quotes through the engine as Django's sqlite3 backend does, in batches the
    # constant limits allow rather than ones read from the stdlib's getlimit().
    def _quote_params_for_last_executed_query(
        self, params: t.Sequence[t.Any]
    ) -> tuple[t.Any, ...]:
        batch_size = min(features.MAX_QUERY_PARAMS, MAX_COLUMNS)
        quoted: tuple[t.Any, ...] = ()
        for start in range(0, len(params), batch_size):
            batch = params[start : start + batch_size]
            driver_cursor = self.connection.connection.cursor()
            try:
                driver_cursor.execute(
                    "SELECT " + ", ".join(["QUOTE(?)"] * len(batch)),
                    cursor.adapt_params(batch),
                )
                quoted += tuple(driver_cursor.fetchone())
            finally:
                driver_cursor.close()
        return quoted
