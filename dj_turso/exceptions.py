import django.db


class TursoBackendError(Exception):
    pass


class CloneNotSupportedError(TursoBackendError, django.db.NotSupportedError):
    def __init__(self) -> None:
        super().__init__(
            "Turso test databases cannot be cloned: two processes cannot open one "
            "database file. Give each test process its own test database instead."
        )
