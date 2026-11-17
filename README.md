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
- A pattern from a column, such as `name__regex=F("pattern")`, is not checked: one Turso
  cannot compile matches no rows.
- `$` matches only at the end of the text, where Python's `re` also matches before a
  final newline. Use `(?m)` for the end of any line.

## Running the tests

The remote transport tests need a local server:

```shell
docker compose up -d --wait
uvx --with tox-uv tox
```
