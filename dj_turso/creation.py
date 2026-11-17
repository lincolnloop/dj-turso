import multiprocessing
import os
import pathlib
import typing as t

from django.db.backends.sqlite3 import creation as sqlite_creation

from dj_turso import drivers

# Files a Turso database keeps beside its main file.
DATABASE_FILE_SUFFIXES = ("", "-wal", "-shm")


class DatabaseCreation(sqlite_creation.DatabaseCreation):
    if t.TYPE_CHECKING:
        # Django's own, left out of django-stubs.
        def _get_database_display_str(
            self, verbosity: int, database_name: str
        ) -> str: ...

    # pyturso has no shared cache: Django's memorydb URI would open a file of that name.
    def _get_test_db_name(self) -> str:
        test_name: str | None = self.connection.settings_dict["TEST"]["NAME"]
        return test_name or ":memory:"

    # sqlite3's clone with VACUUM INTO in place of shutil.copy() and backup(): a copy
    # misses the -wal file, and pyturso has no backup().
    def _clone_test_db(self, suffix: str, verbosity: int, keepdb: bool = False) -> None:
        source_database_name = self.connection.settings_dict["NAME"]
        if drivers.is_remote_name(source_database_name):
            # Django's base refusal, for a backend that cannot clone.
            message = (
                "The database backend doesn't support cloning databases. "
                "Disable the option to run tests in parallel processes."
            )
            raise NotImplementedError(message)
        target_database_name = self.get_test_db_clone_settings(suffix)["NAME"]
        if not self.is_in_memory_db(source_database_name):
            if os.access(target_database_name, os.F_OK):
                if keepdb:
                    return
                if verbosity >= 1:
                    display_name = self._get_database_display_str(
                        verbosity, target_database_name
                    )
                    self.log(
                        f"Destroying old test database for alias {display_name}..."
                    )
                try:
                    remove_database_files(target_database_name)
                except OSError as exc:
                    self.log(f"Got an error deleting the old test database: {exc}")
                    raise SystemExit(2) from exc
            self.vacuum_into(target_database_name)
        # Fork copies an in-memory database; forkserver and spawn open a file.
        elif multiprocessing.get_start_method() in {"forkserver", "spawn"}:
            remove_database_files(target_database_name)
            self.vacuum_into(target_database_name)

    # sqlite3 loads the clone into memory with backup(); the worker opens the file.
    def setup_worker_connection(self, _worker_id: int) -> None:
        settings_dict = self.get_test_db_clone_settings(str(_worker_id))
        self.connection.settings_dict.update(settings_dict)
        self.connection.close()

    def vacuum_into(self, database_name: str) -> None:
        # Turso takes only a string literal path, not a parameter.
        quoted_name = "'{}'".format(database_name.replace("'", "''"))
        with self.connection.cursor() as cursor:
            cursor.execute(f"VACUUM INTO {quoted_name}")

    def _destroy_test_db(self, test_database_name: str, verbosity: int) -> None:
        if not self.is_in_memory_db(test_database_name):
            remove_database_files(test_database_name)


def remove_database_files(name: str) -> None:
    for suffix in DATABASE_FILE_SUFFIXES:
        pathlib.Path(name + suffix).unlink(missing_ok=True)
