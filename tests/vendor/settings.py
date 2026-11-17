"""Settings replicating Django's runtests.py and test_sqlite.py for the vendored apps.

DJ_TURSO_TARGET picks the databases: ``sqlite`` is the stdlib control run.
"""

import copy
import os
import pathlib

from django.utils import log

from scripts import vendor_django

# NAME absent in test_sqlite.py; pytest evaluates skipUnlessDBFeature at
# collection, before the test databases replace it, so it must connect.
TARGET_DATABASES = {
    # conftest.py gives each test database its own file per worker.
    "pyturso": {
        "default": {"ENGINE": "dj_turso", "NAME": ":memory:"},
        "other": {"ENGINE": "dj_turso", "NAME": ":memory:"},
    },
    "sqlite": {
        "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
        "other": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
    },
}

DATABASES = TARGET_DATABASES[os.environ.get("DJ_TURSO_TARGET", "pyturso")]

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django_tests_secret_key")

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

USE_TZ = False

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sites",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.admin.apps.SimpleAdminConfig",
    "django.contrib.staticfiles",
    *vendor_django.read_app_list(pathlib.Path(__file__).with_name("apps.txt"))["apps"],
    "custom_migration_operations",
    "user_commands",
]

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "urls"

STATIC_URL = "static/"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [str(pathlib.Path(__file__).with_name("django") / "templates")],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

LANGUAGE_CODE = "en"

SITE_ID = 1

MIGRATION_MODULES = {
    "auth": None,
    "contenttypes": None,
    "sessions": None,
}

LOGGING = copy.deepcopy(log.DEFAULT_LOGGING)
LOGGING["loggers"]["django"]["level"] = "ERROR"

SILENCED_SYSTEM_CHECKS = [
    "fields.W342",
    "postgres.E005",
]
