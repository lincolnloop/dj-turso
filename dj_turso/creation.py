import pathlib

from django.db.backends.base import creation as base_creation
from django.db.backends.sqlite3 import creation as sqlite_creation

from dj_turso import drivers, exceptions

# Files a Turso database keeps beside its main file.
DATABASE_FILE_SUFFIXES = ("", "-wal", "-shm")


class DatabaseCreation(sqlite_creation.DatabaseCreation):
    def _get_test_db_name(self) -> str:
        test_name: str | None = self.connection.settings_dict["TEST"]["NAME"]
        if test_name:
            return test_name
        name = str(self.connection.settings_dict["NAME"])
        if drivers.is_remote_name(name):
            raise exceptions.RemoteTestNameRequiredError(self.connection.alias)
        if self.is_in_memory_db(name):
            return name
        path = pathlib.Path(name)
        return str(path.with_name(base_creation.TEST_DATABASE_PREFIX + path.name))

    def _clone_test_db(self, suffix: str, verbosity: int, keepdb: bool = False) -> None:
        raise exceptions.CloneNotSupportedError

    def _destroy_test_db(self, test_database_name: str, verbosity: int) -> None:
        if not self.is_in_memory_db(test_database_name):
            remove_database_files(test_database_name)


def remove_database_files(name: str) -> None:
    for suffix in DATABASE_FILE_SUFFIXES:
        pathlib.Path(name + suffix).unlink(missing_ok=True)
