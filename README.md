# dj-turso

<!-- prettier-ignore-start -->
[![CI](https://github.com/lincolnloop/dj-turso/actions/workflows/test.yml/badge.svg?branch=main&event=push)](https://github.com/lincolnloop/dj-turso/actions/workflows/test.yml?query=branch%3Amain+event%3Apush)
[![Coverage](https://img.shields.io/codecov/c/github/lincolnloop/dj-turso.svg)](https://codecov.io/gh/lincolnloop/dj-turso)
[![PyPI](https://img.shields.io/pypi/v/dj-turso.svg)](https://pypi.org/project/dj-turso/)
[![Python versions](https://img.shields.io/pypi/pyversions/dj-turso.svg)](https://pypi.org/project/dj-turso/)
[![Django versions](https://img.shields.io/pypi/frameworkversions/django/dj-turso.svg)](https://pypi.org/project/dj-turso/)
[![License](https://img.shields.io/pypi/l/dj-turso.svg)](https://github.com/lincolnloop/dj-turso/blob/main/LICENSE)
<!-- prettier-ignore-end -->

Django database backend for [Turso](https://turso.tech): local database files and Turso
Cloud through one engine.

Status: in development, not yet released.

```python
DATABASES = {
    "default": {
        "ENGINE": "dj_turso",
        "NAME": "default.db",
    },
}
```

For a Turso Cloud database on the Turso engine, install the `serverless` extra:

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

A database on the libSQL engine, such as a Turso Cloud database of type SQLite or an
sqld server, is refused on first connection:

```text
NotSupportedError: dj-turso requires the Turso engine: the database has no turso_version() function.
```

## One process per database file

A local database file opens in one process at a time, as
[Turso documents](https://docs.turso.tech/sql-reference/multiprocess-access). A second
process that opens the same file fails:

```text
OperationalError: Locking error: Failed locking file '/srv/app/default.db'. File is locked by another process
```

Threads within one process share the file freely. So a local file suits development,
tests and single-process apps, but not a server that runs several worker processes, such
as gunicorn or uwsgi with more than one worker. Point those at Turso Cloud. A management
command run while the server holds the file fails the same way.

## Testing your project

Run your tests in parallel with Django's runner or with
[pytest-django](https://pytest-django.readthedocs.io) and
[pytest-xdist](https://pytest-xdist.readthedocs.io). Each worker gets its own test
database:

```shell
python manage.py test --parallel
```

```shell
pip install pytest-django pytest-xdist
pytest -n auto
```

Without `TEST["NAME"]`, the test database is a temporary file that all threads share,
and Django's runner gives each worker a copy. pytest-xdist workers each get a file
instead: pytest-django names it `TEST["NAME"]`, or `test_` and `NAME`, with the worker
appended, such as `test_default.db_gw0`.

As with Django's sqlite3 in-memory default, the temporary file never outlives the run:
`--keepdb`, and pytest-django's `--reuse-db`, keep only a test database named in
`TEST["NAME"]`.

So with a URL `NAME`, as for Turso Cloud, point the test database at a file:

```python
DATABASES = {
    "default": {
        "ENGINE": "dj_turso",
        "NAME": "turso://my-database.turso.io",
        "OPTIONS": {"auth_token": os.environ["TURSO_AUTH_TOKEN"]},
        "TEST": {"NAME": "test_default.db"},
    },
}
```

Or at a Turso Cloud database. dj-turso migrates it in place and never creates or
destroys it:

```python
"TEST": {"NAME": "turso://my-test-database.turso.io"},
```

Run tests against a remote test database without `-n` or `--parallel`: each worker needs
its own test database, and a remote one can't be renamed or cloned.

## Regular expressions

`regex` and `iregex` lookups run on Turso's `REGEXP`, which follows the syntax of
[Rust's regex crate](https://docs.rs/regex/latest/regex/#syntax), not Python's `re`:

```python
Tag.objects.filter(name__regex=r"^ba[rz]")  # works
Tag.objects.filter(name__regex=r"b(.).*b\1")  # raises NotSupportedError
```

- A pattern Turso cannot compile, such as a backreference or lookaround, raises
  `NotSupportedError` before the query runs. Each pattern is checked against the
  database once per process.
- Only a literal pattern, a string or a `Value()`, is checked. Any other pattern, such
  as a column's in `name__regex=F("pattern")`, a function's or a subquery's, is not: one
  Turso cannot compile matches no rows.
- `$` matches only at the end of the text, where Python's `re` also matches before a
  final newline. Use `(?m)` for the end of any line.
- A possessive quantifier such as `a*+` runs, but as a repetition repeated, not a
  possessive one: `a*+a` matches `aa` on Turso, where Python's `re` matches nothing.

## Beyond Django's SQLite backend

dj-turso runs these, where Django's sqlite3 backend raises or computes otherwise:

- `Round` to a negative precision rounds left of the decimal point. Django's sqlite3
  backend raises `ValueError`.

  ```python
  Product.objects.annotate(rounded=Round("price", -1))  # 365 rounds to 370
  ```

- `Round` to a precision given as an expression, such as a column's, which may be
  negative. A dj-turso extension: Django documents the precision as an integer on every
  backend. Django's sqlite3 backend runs it, but rounds to a negative precision as to 0.

  ```python
  Product.objects.annotate(rounded=Round("price", F("places")))  # places may be -1
  ```

## Unsupported features

Each of these raises `NotSupportedError`, where Django's sqlite3 backend would run the
query:

- `MD5`, `SHA1`, `SHA224`, `SHA256`, `SHA384` and `SHA512`: Turso has no hash functions.
- Converting a datetime to another time zone. With `USE_TZ`, Turso's date functions work
  only in the database's time zone, UTC unless the database's `TIME_ZONE` says
  otherwise. While another time zone is active, a datetime's `__date`, `__hour` and
  similar lookups, `Extract`, `Trunc` and `datetimes()` raise, as does a `tzinfo`
  argument naming another zone:

  ```python
  Event.objects.filter(start__hour=9)  # works in UTC
  with timezone.override("America/Chicago"):
      Event.objects.filter(start__hour=9)  # raises NotSupportedError
  ```

- An `order_by` on an aggregate, such as
  `StringAgg("name", Value(","), order_by="name")`: Turso takes no `ORDER BY` inside an
  aggregate.
- A regular expression Turso cannot compile: see
  [Regular expressions](#regular-expressions).

`GeneratedField` fails Django's database system checks, which `migrate` runs: Turso's
generated columns are experimental and off by default.

Division by zero, including `Cot(0)` and dividing a duration, returns `NULL`, as in
SQLite and Turso.

A raw cursor returns date and time columns as the stored text, where Django's sqlite3
backend returns `date`, `datetime` and `time` objects: Turso's drivers report no
declared column types for Django to convert by.

## Known Turso bugs

dj-turso does not work around these yet:

- A subquery inside `HAVING` returns the wrong rows, silently:
  [turso#10665](https://github.com/tursodatabase/turso/issues/10665).
- `union()`, `intersection()` or `difference()` inside a `WHERE` subquery is rejected:
  [turso#8550](https://github.com/tursodatabase/turso/issues/8550).
- An `UPDATE` that refers to a mixed-case quoted table name by a qualified column is
  rejected. Fixed upstream, awaiting a pyturso release:
  [turso#10952](https://github.com/tursodatabase/turso/pull/10952).
- `PRAGMA foreign_keys = OFF` applies inside a transaction, where SQLite ignores it, so
  constraint checks can be off where Django expects them on. Fixed upstream, awaiting a
  pyturso release: [turso#11095](https://github.com/tursodatabase/turso/pull/11095).
- Renaming a column to a name that differs only in case is rejected as a duplicate
  column: [turso#9429](https://github.com/tursodatabase/turso/issues/9429).

## Running the tests

The remote tests run against a local stub of Turso Cloud's HTTP API, so they need no
server:

```shell
uvx --with tox-uv tox
```

The Cloud tests create a Turso Cloud database for each test process, and delete it when
the process ends. They need a
[Platform API token](https://docs.turso.tech/api-reference) that can create, read and
delete databases in a group and mint their tokens, and skip without one:

```shell
TURSO_API_TOKEN=...
TURSO_ORG=my-organization
TURSO_TEST_GROUP=my-test-group
```

Each database is named `djturso-test-` and the time it was created, and a run deletes
any in the group more than an hour old, left by a run that crashed.

CI passes `TURSO_API_TOKEN` from a repository secret, and `TURSO_ORG` and
`TURSO_TEST_GROUP` from repository variables, so the Cloud tests skip on pull requests
from forks. Locally, export them from a `.env` file:

```shell
set -a; . ./.env; set +a
uvx --with tox-uv tox
```
