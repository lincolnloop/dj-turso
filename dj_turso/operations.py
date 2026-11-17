import typing as t

from django.db.backends.base import operations as base_operations
from django.db.backends.sqlite3 import operations as sqlite_operations

from dj_turso import cursor, exceptions, features

# SQLite's compile-time default for SQLITE_MAX_COLUMN, which Turso keeps.
MAX_COLUMNS = 2000

# Native SQL for the parts Django's sqlite3 backend extracts with Python functions.
# Each {value} is the operand; "%%" survives the driver's placeholder conversion.
EXTRACT_TEMPLATES = {
    "year": "CAST(STRFTIME('%%Y', {value}) AS INTEGER)",
    "iso_year": "CAST(STRFTIME('%%G', {value}) AS INTEGER)",
    "quarter": "((CAST(STRFTIME('%%m', {value}) AS INTEGER) + 2) / 3)",
    "month": "CAST(STRFTIME('%%m', {value}) AS INTEGER)",
    "week": "CAST(STRFTIME('%%V', {value}) AS INTEGER)",
    "week_day": "(CAST(STRFTIME('%%w', {value}) AS INTEGER) + 1)",
    "iso_week_day": "CAST(STRFTIME('%%u', {value}) AS INTEGER)",
    "day": "CAST(STRFTIME('%%d', {value}) AS INTEGER)",
    "hour": "CAST(STRFTIME('%%H', {value}) AS INTEGER)",
    "minute": "CAST(STRFTIME('%%M', {value}) AS INTEGER)",
    "second": "CAST(STRFTIME('%%S', {value}) AS INTEGER)",
}


class DatabaseOperations(sqlite_operations.DatabaseOperations):
    # Django's sqlite3 backend gates this on the stdlib's SQLite; Turso's dialect is
    # past the 3.39 fix it checks for.
    force_group_by = base_operations.BaseDatabaseOperations.force_group_by

    def date_extract_sql(
        self, lookup_type: str, sql: str, params: t.Sequence[t.Any]
    ) -> tuple[str, tuple[t.Any, ...]]:
        return get_extract_sql(lookup_type, "date", sql, params)

    def datetime_extract_sql(
        self, lookup_type: str, sql: str, params: t.Sequence[t.Any], tzname: str | None
    ) -> tuple[str, tuple[t.Any, ...]]:
        self.check_time_zone(tzname)
        return get_extract_sql(lookup_type, "datetime", sql, params)

    # django-stubs types this as a bare string; Django returns (sql, params).
    def time_extract_sql(
        self, lookup_type: str, sql: str, params: t.Sequence[t.Any]
    ) -> t.Any:
        return get_extract_sql(lookup_type, "time", sql, params)

    # Values are stored in the database time zone, and Turso's date functions know
    # no other: any conversion would need a time zone database.
    # Django passes no tzname without USE_TZ.
    def check_time_zone(self, tzname: str | None) -> None:
        database_tzname = self.connection.timezone_name
        if tzname is not None and tzname != database_tzname:
            raise exceptions.TimeZoneConversionNotSupportedError(
                tzname, database_tzname
            )

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


def get_extract_sql(
    lookup_type: str, value_type: str, sql: str, params: t.Sequence[t.Any]
) -> tuple[str, tuple[t.Any, ...]]:
    try:
        template = EXTRACT_TEMPLATES[lookup_type]
    except KeyError as exc:
        raise exceptions.UnknownLookupTypeError(lookup_type, value_type) from exc
    return fill_template(template, sql, params)


# The operand's parameters repeat once for each place it appears.
def fill_template(
    template: str, sql: str, params: t.Sequence[t.Any]
) -> tuple[str, tuple[t.Any, ...]]:
    return template.format(value=sql), tuple(params) * template.count("{value}")
