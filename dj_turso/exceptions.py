import django.core.exceptions
import django.db


class TursoBackendError(Exception):
    pass


class CloneNotSupportedError(TursoBackendError, django.db.NotSupportedError):
    def __init__(self) -> None:
        super().__init__(
            "Turso test databases cannot be cloned: two processes cannot open one "
            "database file. Give each test process its own test database instead."
        )


class RemoteTestNameRequiredError(
    TursoBackendError, django.core.exceptions.ImproperlyConfigured
):
    def __init__(self, alias: str) -> None:
        super().__init__(
            f"Database {alias!r} has a URL NAME, so its TEST NAME must be set: "
            "dj-turso never creates or destroys remote databases. Use a local "
            "file path, or the URL of a database the tests own."
        )


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
