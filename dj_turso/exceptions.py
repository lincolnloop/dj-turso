import django.db


class TursoBackendError(Exception):
    pass


# Stdlib sqlite3's error and message for an integer it cannot bind.
class IntegerOverflowError(TursoBackendError, OverflowError):
    def __init__(self) -> None:
        super().__init__("Python int too large to convert to SQLite INTEGER")


class HashFunctionNotSupportedError(TursoBackendError, django.db.NotSupportedError):
    def __init__(self, function: str) -> None:
        super().__init__(f"Turso has no {function} function to hash with.")


class TimeZoneConversionNotSupportedError(
    TursoBackendError, django.db.NotSupportedError
):
    def __init__(self, tzname: str, database_tzname: str) -> None:
        super().__init__(
            "Turso's date functions cannot convert from the database time zone "
            f"{database_tzname!r} to {tzname!r}. Query in {database_tzname!r} instead."
        )


class UnknownLookupTypeError(TursoBackendError, ValueError):
    def __init__(self, lookup_type: str, value_type: str) -> None:
        super().__init__(f"Turso cannot extract {lookup_type!r} from a {value_type}.")


# A database error, as Django's other backends raise for an unknown unit.
class UnknownTruncKindError(TursoBackendError, django.db.DataError):
    def __init__(self, kind: str, value_type: str) -> None:
        super().__init__(f"Turso cannot truncate a {value_type} to {kind!r}.")


class RegexNotSupportedError(TursoBackendError, django.db.NotSupportedError):
    def __init__(self, pattern: str) -> None:
        super().__init__(
            f"Turso cannot compile the regular expression {pattern!r}: its REGEXP "
            "follows the syntax of Rust's regex crate, not Python's re module."
        )
