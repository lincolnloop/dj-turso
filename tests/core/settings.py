import os
import typing as t

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "test-only-not-secret")

# The test run moves both names under its own temporary directory.
DATABASES: dict[str, dict[str, t.Any]] = {
    "default": {"ENGINE": "dj_turso", "NAME": "default.db"}
}

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

INSTALLED_APPS = ["tests.core"]

USE_TZ = True
