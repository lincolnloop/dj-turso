# Polls

[Django's tutorial](https://docs.djangoproject.com/en/6.1/intro/tutorial01/) polls app,
on a local Turso database file, `default.db`:

```shell
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Open http://localhost:8000/admin/ to add a question and its choices, then
http://localhost:8000/polls/ to vote.

## Turso Cloud

Set `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN`, then run the same commands:

```shell
export TURSO_DATABASE_URL=turso://my-database.turso.io
export TURSO_AUTH_TOKEN=...
uv run python manage.py migrate
```

The database must be on the Turso engine.

## One process at a time

A local file opens in one process at a time, so use `runserver` locally, and stop it
before running another management command. A server with several worker processes, such
as gunicorn, needs Turso Cloud.

## Settings

| Variable               | Default                                        |
| ---------------------- | ---------------------------------------------- |
| `TURSO_DATABASE_URL`   | unset: the local file `default.db`             |
| `TURSO_AUTH_TOKEN`     | unset: no token                                |
| `DJANGO_SECRET_KEY`    | an insecure key for local development          |
| `DJANGO_DEBUG`         | `True`; any other value turns it off           |
| `ENABLE_DEBUG_TOOLBAR` | `1`: Django Debug Toolbar on; `0` turns it off |

## Tests

```shell
uv run pytest
```

The tests run on a temporary file database, even with `TURSO_DATABASE_URL` set, so they
never touch Turso Cloud.
