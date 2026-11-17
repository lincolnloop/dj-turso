import collections.abc
import pathlib

import django.db.utils
import pytest
import pytest_django
from django.db.backends.base import base


@pytest.fixture(params=[pytest.param("pyturso", id="pyturso")])
def target(
    django_db_blocker: pytest_django.DjangoDbBlocker, tmp_path: pathlib.Path
) -> collections.abc.Iterator[base.BaseDatabaseWrapper]:
    handler = django.db.utils.ConnectionHandler(
        {"default": {"ENGINE": "dj_turso", "NAME": str(tmp_path / "default.db")}}
    )
    connection = handler["default"]
    with django_db_blocker.unblock():
        yield connection
        connection.close()
