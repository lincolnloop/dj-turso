import atexit
import multiprocessing
import os
import pathlib
import shutil
import tempfile
import typing as t

from django.db.backends.base import base
from django.db.backends.sqlite3 import creation as sqlite_creation

from dj_turso import drivers

# Files a Turso database keeps beside its main file.
DATABASE_FILE_SUFFIXES = ("", "-wal", "-shm")


# Django's own methods, left out of django-stubs.
if t.TYPE_CHECKING:

    class SqliteDatabaseCreation(sqlite_creation.DatabaseCreation):
        def _create_test_db(
            self, verbosity: int, autoclobber: bool, keepdb: bool = False
        ) -> str: ...

        def _get_database_display_str(
            self, verbosity: int, database_name: str
        ) -> str: ...

else:
    SqliteDatabaseCreation = sqlite_creation.DatabaseCreation


class DatabaseCreation(SqliteDatabaseCreation):
    def __init__(self, connection: base.BaseDatabaseWrapper) -> None:
        super().__init__(connection)
        self.default_test_db_name: str | None = None

    # dj-turso never creates or destroys a remote test database: Django runs these as
    # with keepdb. create_test_db() stays Django's, for its warnings' stack level.
    def _create_test_db(
        self, verbosity: int, autoclobber: bool, keepdb: bool = False
    ) -> str:
        keepdb = keepdb or drivers.is_remote_name(self._get_test_db_name())
        return super()._create_test_db(verbosity, autoclobber, keepdb)

    def destroy_test_db(
        self,
        old_database_name: str | None = None,
        verbosity: int = 1,
        keepdb: bool = False,
        suffix: str | None = None,
    ) -> None:
        test_database_name = self.connection.settings_dict["NAME"]
        keepdb = keepdb or drivers.is_remote_name(test_database_name)
        # Like sqlite3's in-memory default, kept or not, it ends with the run.
        if keepdb and test_database_name == self.default_test_db_name:
            if suffix is not None:
                test_database_name = self.get_test_db_clone_settings(suffix)["NAME"]
            super().destroy_test_db(old_database_name, verbosity, keepdb, suffix)
            self._destroy_test_db(test_database_name, verbosity)
        else:
            super().destroy_test_db(old_database_name, verbosity, keepdb, suffix)

    # pyturso has no shared cache, so each connection to ":memory:" is a database of its
    # own: a temporary file, in a private directory, is the one every thread opens.
    def _get_test_db_name(self) -> str:
        test_name: str | None = self.connection.settings_dict["TEST"]["NAME"]
        if test_name:
            return test_name
        if self.default_test_db_name is None:
            alias = self.connection.alias
            directory = tempfile.mkdtemp(prefix=f"test_{alias}_")
            # pytest-django's --reuse-db never destroys the test database.
            atexit.register(shutil.rmtree, directory, ignore_errors=True)
            self.default_test_db_name = str(
                pathlib.Path(directory) / f"test_{alias}.db"
            )
        return self.default_test_db_name

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
        if test_database_name == self.default_test_db_name:
            pathlib.Path(test_database_name).parent.rmdir()


def remove_database_files(name: str) -> None:
    for suffix in DATABASE_FILE_SUFFIXES:
        pathlib.Path(name + suffix).unlink(missing_ok=True)
