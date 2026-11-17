import pathlib

from django.db.backends.sqlite3 import creation as sqlite_creation

# Files a Turso database keeps beside its main file.
DATABASE_FILE_SUFFIXES = ("", "-wal", "-shm")


class DatabaseCreation(sqlite_creation.DatabaseCreation):
    # pyturso has no shared cache: Django's memorydb URI would open a file of that name.
    def _get_test_db_name(self) -> str:
        test_name: str | None = self.connection.settings_dict["TEST"]["NAME"]
        return test_name or ":memory:"

    # Django's refusal, not sqlite3's clone: that needs backup(), which pyturso lacks.
    def _clone_test_db(self, suffix: str, verbosity: int, keepdb: bool = False) -> None:
        super(sqlite_creation.DatabaseCreation, self)._clone_test_db(  # type: ignore[misc]
            suffix, verbosity, keepdb
        )

    def _destroy_test_db(self, test_database_name: str, verbosity: int) -> None:
        if not self.is_in_memory_db(test_database_name):
            remove_database_files(test_database_name)


def remove_database_files(name: str) -> None:
    for suffix in DATABASE_FILE_SUFFIXES:
        pathlib.Path(name + suffix).unlink(missing_ok=True)
