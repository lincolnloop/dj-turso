import os

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "test-only-not-secret")

DATABASES: dict[str, dict[str, str]] = {}

INSTALLED_APPS: list[str] = []

USE_TZ = True
