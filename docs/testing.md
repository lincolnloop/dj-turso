# Testing your project

Run your tests as usual, with Django's runner or with
[pytest-django](https://pytest-django.readthedocs.io). No test settings are needed:

```shell
python manage.py test
```

The test database is a temporary file that never outlives the run, as with Django's
sqlite3 in-memory default. `--keepdb`, and pytest-django's `--reuse-db`, keep only a
test database named in `TEST["NAME"]`.

## Turso Cloud

With a URL `NAME`, point the test database at a local file:

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

## Parallel runs

Parallel runs are supported, with Django's `--parallel` and with
[pytest-xdist](https://pytest-xdist.readthedocs.io). Each worker gets its own test
database:

```shell
python manage.py test --parallel
pytest -n auto
```

- Django's runner gives each worker a copy of the temporary file.
- pytest-xdist workers each get a file named after `TEST["NAME"]`, or `test_` and
  `NAME`, with the worker appended, such as `test_default.db_gw0`. So set `TEST["NAME"]`
  when `NAME` is an absolute path or a URL: `test_/srv/app/default.db_gw0` is no file.
- A remote test database runs without `-n` or `--parallel`: it can't be renamed or
  cloned per worker.
