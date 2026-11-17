# dj-turso

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

For Turso Cloud, install the `serverless` extra:

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

## One process per database file

A local database file opens in one process at a time, as
[Turso documents](https://docs.turso.tech/sql-reference/multiprocess-access). A second
process that opens the same file fails:

```text
OperationalError: Locking error: Failed locking file '/srv/app/default.db'. File is locked by another process
```

Threads within one process share the file freely. So a local file suits development,
tests and single-process apps, but not a server that runs several worker processes, such
as gunicorn or uwsgi with more than one worker. Point those at a remote database, on
sqld or Turso Cloud. A management command run while the server holds the file fails the
same way.

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

Django's runner tests in memory, unless `TEST["NAME"]` names a test database, and gives
each worker a copy. pytest-xdist workers each get a file instead: pytest-django names it
`TEST["NAME"]`, or `test_` and `NAME`, with the worker appended, such as
`test_default.db_gw0`. It leaves a `TEST["NAME"]` of `":memory:"` in memory.

So with a URL `NAME`, as for Turso Cloud, set the test database in memory:

```python
DATABASES = {
    "default": {
        "ENGINE": "dj_turso",
        "NAME": "turso://my-database.turso.io",
        "OPTIONS": {"auth_token": os.environ["TURSO_AUTH_TOKEN"]},
        "TEST": {"NAME": ":memory:"},
    },
}
```

Or point the test database at a file:

```python
DATABASES = {
    "default": {
        "ENGINE": "dj_turso",
        "NAME": "default.db",
        "TEST": {"NAME": "test_default.db"},
    },
}
```

Or at a remote database, on sqld or Turso Cloud. dj-turso migrates it in place and never
creates or destroys it:

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

## Running the tests

The remote transport tests need a local server:

```shell
docker compose up -d --wait
uvx --with tox-uv tox
```
