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
