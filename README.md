# dj-turso

<!-- prettier-ignore-start -->
[![CI](https://github.com/lincolnloop/dj-turso/actions/workflows/test.yml/badge.svg?branch=main&event=push)](https://github.com/lincolnloop/dj-turso/actions/workflows/test.yml?query=branch%3Amain+event%3Apush)
[![Coverage](https://img.shields.io/codecov/c/github/lincolnloop/dj-turso.svg)](https://codecov.io/gh/lincolnloop/dj-turso)
[![PyPI](https://img.shields.io/pypi/v/dj-turso.svg)](https://pypi.org/project/dj-turso/)
[![Python versions](https://img.shields.io/pypi/pyversions/dj-turso.svg)](https://pypi.org/project/dj-turso/)
[![Django versions](https://img.shields.io/pypi/frameworkversions/django/dj-turso.svg)](https://pypi.org/project/dj-turso/)
[![License](https://img.shields.io/pypi/l/dj-turso.svg)](https://github.com/lincolnloop/dj-turso/blob/main/LICENSE)
<!-- prettier-ignore-end -->

A Django database backend for [Turso](https://turso.tech), the Rust rewrite of SQLite.
Use a local database file in development and Turso Cloud in production, with one engine.

**Status: alpha.** Requires Django 6.1 and Python 3.12+.

## Install

```shell
pip install dj-turso
```

## Use a local file

```python
DATABASES = {
    "default": {
        "ENGINE": "dj_turso",
        "NAME": "default.db",
    },
}
```

## Use Turso Cloud

```shell
pip install "dj-turso[serverless]"
```

```python
DATABASES = {
    "default": {
        "ENGINE": "dj_turso",
        "NAME": "turso://my-database.turso.io",
        "OPTIONS": {"auth_token": os.environ["TURSO_AUTH_TOKEN"]},
    },
}
```

## Good to know

- A local file opens in one process at a time: fine for `runserver` and tests, not for a
  server with several workers. Use Turso Cloud there.
- A Turso Cloud database must be on the Turso engine, not libSQL.
- Date lookups need the active time zone to match the database's, UTC by default.

## Learn more

- [Testing your project](https://github.com/lincolnloop/dj-turso/blob/main/docs/testing.md)
- [Compatibility](https://github.com/lincolnloop/dj-turso/blob/main/docs/compatibility.md):
  regular expressions, unsupported features, known Turso bugs
- [Example project](https://github.com/lincolnloop/dj-turso/tree/main/examples/polls)
- [Contributing](https://github.com/lincolnloop/dj-turso/blob/main/CONTRIBUTING.md)
- [Changelog](https://github.com/lincolnloop/dj-turso/releases)
