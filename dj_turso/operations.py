import re
import typing as t

from django.db.backends.base import operations as base_operations
from django.db.backends.sqlite3 import operations as sqlite_operations

from dj_turso import cursor, exceptions, features

PLACEHOLDER_REGEX = re.compile(r"\{(\w+)\}")

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

# Months back to the quarter's first, and days back to Monday.
MONTHS_INTO_QUARTER = "(CAST(STRFTIME('%%m', {value}) AS INTEGER) - 1) %% 3"
DAYS_INTO_WEEK = "(CAST(STRFTIME('%%w', {value}) AS INTEGER) + 6) %% 7"

DATE_TRUNC_TEMPLATES = {
    "year": "STRFTIME('%%Y-01-01', {value})",
    "quarter": (
        "DATE({value}, 'start of month', "
        f"PRINTF('-%%d months', {MONTHS_INTO_QUARTER}))"
    ),
    "month": "STRFTIME('%%Y-%%m-01', {value})",
    "week": f"DATE({{value}}, PRINTF('-%%d days', {DAYS_INTO_WEEK}))",
    "day": "DATE({value})",
}

DATETIME_TRUNC_TEMPLATES = {
    "year": "STRFTIME('%%Y-01-01 00:00:00', {value})",
    "quarter": (
        "DATETIME({value}, 'start of month', "
        f"PRINTF('-%%d months', {MONTHS_INTO_QUARTER}))"
    ),
    "month": "STRFTIME('%%Y-%%m-01 00:00:00', {value})",
    "week": (
        f"DATETIME({{value}}, 'start of day', PRINTF('-%%d days', {DAYS_INTO_WEEK}))"
    ),
    "day": "STRFTIME('%%Y-%%m-%%d 00:00:00', {value})",
    "hour": "STRFTIME('%%Y-%%m-%%d %%H:00:00', {value})",
    "minute": "STRFTIME('%%Y-%%m-%%d %%H:%%M:00', {value})",
    "second": "STRFTIME('%%Y-%%m-%%d %%H:%%M:%%S', {value})",
}

TIME_TRUNC_TEMPLATES = {
    "hour": "STRFTIME('%%H:00:00', {value})",
    "minute": "STRFTIME('%%H:%%M:00', {value})",
    "second": "STRFTIME('%%H:%%M:%%S', {value})",
}

# Date functions keep milliseconds at most; Django stores and compares microseconds.
FRACTION_MICROSECONDS_TEMPLATE = (
    "CASE WHEN INSTR({value}, '.') THEN "
    "CAST(SUBSTR(SUBSTR({value}, INSTR({value}, '.') + 1) || '000000', 1, 6) "
    "AS INTEGER) ELSE 0 END"
)

DATETIME_CAST_TIME_TEMPLATE = (
    "TIME({value}) || CASE WHEN INSTR({value}, '.') THEN "
    f"PRINTF('.%%06d', {FRACTION_MICROSECONDS_TEMPLATE}) ELSE '' END"
)

# Microseconds since the Unix epoch; a bare time counts from 2000-01-01.
EPOCH_MICROSECONDS_TEMPLATE = (
    f"(UNIXEPOCH({{value}}) * 1000000 + {FRACTION_MICROSECONDS_TEMPLATE})"
)

# Back from epoch microseconds to the form of the temporal operand: datetime, bare
# time (wrapping at midnight) or date (whole days, as Django's date + timedelta).
MICROSECONDS = "((({total} %% 1000000) + 1000000) %% 1000000)"
FROM_EPOCH_MICROSECONDS = (
    "STRFTIME(CASE WHEN INSTR({temporal}, ' ') THEN '%%Y-%%m-%%d %%H:%%M:%%S' "
    "WHEN INSTR({temporal}, ':') THEN '%%H:%%M:%%S' ELSE '%%Y-%%m-%%d' END, "
    f"({{total}} - {MICROSECONDS}) / 1000000, 'unixepoch') "
    "|| CASE WHEN INSTR({temporal}, ':') THEN "
    f"REPLACE(PRINTF('.%%06d', {MICROSECONDS}), '.000000', '') ELSE '' END"
)


class DatabaseOperations(sqlite_operations.DatabaseOperations):
    # Django's sqlite3 backend gates this on the stdlib's SQLite; Turso's dialect is
    # past the 3.39 fix it checks for.
    force_group_by = base_operations.BaseDatabaseOperations.force_group_by
    compiler_module = "dj_turso.compiler"

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

    def date_trunc_sql(
        self,
        lookup_type: str,
        sql: str,
        params: t.Sequence[t.Any],
        tzname: str | None = None,
    ) -> tuple[str, tuple[t.Any, ...]]:
        self.check_time_zone(tzname)
        return get_trunc_sql(DATE_TRUNC_TEMPLATES, lookup_type, "date", sql, params)

    # django-stubs types this as a bare string; Django returns (sql, params).
    def datetime_trunc_sql(
        self, lookup_type: str, sql: str, params: t.Sequence[t.Any], tzname: str | None
    ) -> t.Any:
        self.check_time_zone(tzname)
        return get_trunc_sql(
            DATETIME_TRUNC_TEMPLATES, lookup_type, "datetime", sql, params
        )

    # django-stubs types this as a bare string; Django returns (sql, params).
    def time_trunc_sql(
        self,
        lookup_type: str,
        sql: str,
        params: t.Sequence[t.Any],
        tzname: str | None = None,
    ) -> t.Any:
        self.check_time_zone(tzname)
        return get_trunc_sql(TIME_TRUNC_TEMPLATES, lookup_type, "time", sql, params)

    def datetime_cast_date_sql(
        self, sql: str, params: t.Sequence[t.Any], tzname: str | None
    ) -> tuple[str, tuple[t.Any, ...]]:
        self.check_time_zone(tzname)
        return fill_template("DATE({value})", value=(sql, params))

    def datetime_cast_time_sql(
        self, sql: str, params: t.Sequence[t.Any], tzname: str | None
    ) -> tuple[str, tuple[t.Any, ...]]:
        self.check_time_zone(tzname)
        return fill_template(DATETIME_CAST_TIME_TEMPLATE, value=(sql, params))

    # Values are stored in the database time zone, and Turso's date functions know
    # no other: any conversion would need a time zone database.
    # Date arithmetic repeats its operands, with their parameters, so the compiler
    # writes it with get_date_arithmetic_sql(). sqlite3's raises for the rest.
    def combine_duration_expression(
        self, connector: str, sub_expressions: list[str]
    ) -> str:
        lhs, rhs = sub_expressions
        if connector == "*":
            return f"{lhs} * {rhs}"
        if connector == "/":
            return f"CAST({lhs} AS REAL) / {rhs}"
        return super().combine_duration_expression(connector, sub_expressions)

    def subtract_temporals(
        self,
        internal_type: str,
        lhs: tuple[str, t.Sequence[t.Any]],
        rhs: tuple[str, t.Sequence[t.Any]],
    ) -> tuple[str, tuple[t.Any, ...]]:
        return fill_template(
            f"({EPOCH_MICROSECONDS_TEMPLATE.format(value='{lhs}')} - "
            f"{EPOCH_MICROSECONDS_TEMPLATE.format(value='{rhs}')})",
            lhs=lhs,
            rhs=rhs,
        )

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


def get_date_arithmetic_sql(
    connector: str,
    temporal: tuple[str, t.Sequence[t.Any]],
    duration: tuple[str, t.Sequence[t.Any]],
) -> tuple[str, tuple[t.Any, ...]]:
    epoch = EPOCH_MICROSECONDS_TEMPLATE.format(value="{temporal}")
    total = fill_template(
        f"({epoch} {connector} {{duration}})", duration=duration, temporal=temporal
    )
    return fill_template(FROM_EPOCH_MICROSECONDS, temporal=temporal, total=total)


def get_extract_sql(
    lookup_type: str, value_type: str, sql: str, params: t.Sequence[t.Any]
) -> tuple[str, tuple[t.Any, ...]]:
    try:
        template = EXTRACT_TEMPLATES[lookup_type]
    except KeyError as exc:
        raise exceptions.UnknownLookupTypeError(lookup_type, value_type) from exc
    return fill_template(template, value=(sql, params))


def get_trunc_sql(
    templates: dict[str, str],
    kind: str,
    value_type: str,
    sql: str,
    params: t.Sequence[t.Any],
) -> tuple[str, tuple[t.Any, ...]]:
    try:
        template = templates[kind]
    except KeyError as exc:
        raise exceptions.UnknownTruncKindError(kind, value_type) from exc
    return fill_template(template, value=(sql, params))


# An operand's parameters repeat once for each place it appears, in that order.
def fill_template(
    template: str, **operands: tuple[str, t.Sequence[t.Any]]
) -> tuple[str, tuple[t.Any, ...]]:
    params = [
        param
        for name in PLACEHOLDER_REGEX.findall(template)
        for param in operands[name][1]
    ]
    return template.format(**{name: sql for name, (sql, _) in operands.items()}), (
        tuple(params)
    )
