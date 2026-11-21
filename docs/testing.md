# Testing your project

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

With pytest-xdist, set `TEST["NAME"]` whenever `NAME` is an absolute path or a URL:
pytest-django builds the worker's name from it, and `test_/srv/app/default.db_gw0` is no
file. Without `-n`, no `TEST` setting is needed.

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
