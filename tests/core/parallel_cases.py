"""Cases for Django's own runner, run in parallel by test_creation.py."""

import os

import django.db
from django import test
from django.test import runner

from tests.core import models


class FirstTests(test.TestCase):
    def test_writes_to_its_worker_database(self) -> None:
        models.Tag.objects.create(name="first")

        assert django.db.connection.settings_dict["NAME"] == os.environ[
            "WORKER_DATABASE_NAME"
        ].format(worker_id=vars(runner)["_worker_id"])
        assert list(models.Tag.objects.values_list("name", flat=True)) == ["first"]


class SecondTests(test.TestCase):
    def test_writes_to_its_worker_database(self) -> None:
        models.Tag.objects.create(name="second")

        assert django.db.connection.settings_dict["NAME"] == os.environ[
            "WORKER_DATABASE_NAME"
        ].format(worker_id=vars(runner)["_worker_id"])
        assert list(models.Tag.objects.values_list("name", flat=True)) == ["second"]
