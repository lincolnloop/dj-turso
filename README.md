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

## Running the tests

The remote transport tests need a local server:

```shell
docker compose up -d --wait
uvx --with tox-uv tox
```
