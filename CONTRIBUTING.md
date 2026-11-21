# Contributing

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

`tox` runs every suite:

| Environment                                  | Suite                                                      |
| -------------------------------------------- | ---------------------------------------------------------- |
| `py312-django61`, `py315-django61`           | `tests/core` and `tests/tooling`, lowest and highest deps  |
| `dev`                                        | the same, on the locked dependencies                       |
| `vendor-pyturso`                             | the vendored Django suite on dj-turso                      |
| `vendor-sqlite`                              | the same suite on Django's own sqlite3 backend, as control |
| `py312-django61-mypy`, `py315-django61-mypy` | mypy                                                       |

A bare `pytest` runs the vendored suite. The vendored tests are never edited; see
[`tests/vendor/VENDORED.md`](tests/vendor/VENDORED.md).

## Pull requests

Pull request titles follow [Conventional Commits](https://www.conventionalcommits.org),
such as `fix: ...` or `docs: ...`: they become the changelog.
