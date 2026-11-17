# Vendored Django tests

`django/` holds a subset of Django's own test suite, copied unedited. Never edit it;
record gaps in the index instead.

- Upstream: <https://github.com/django/django>
- Tag: `6.1.3`
- Commit: `f16e25dcb9fe55734ba4ea2b00ac34405b7df74a`

## Contents

Entries come from [`apps.txt`](apps.txt).

Apps (collected as tests): `aggregation`, `aggregation_regress`, `annotations`,
`backends`, `bulk_create`, `constraints`, `datetimes`, `db_functions`, `expressions`,
`expressions_case`, `expressions_window`, `fixtures`, `fixtures_regress`, `indexes`,
`inspectdb`, `introspection`, `lookup`, `migrations`, `model_fields`,
`multiple_database`, `queries`, `schema`, `serializers`, `test_utils`, `timezones`,
`transaction_hooks`, `transactions`.

Support (imported or installed by the apps' tests, not collected): `basic`,
`custom_migration_operations`, `import_error_package`, `migrations2`, `templates`,
`urls.py`, `user_commands`.

## Refresh

Check out the Django tag, edit `apps.txt` if needed, then from the repo root:

```sh
uv run python scripts/vendor_django.py --source <django checkout>/tests
```

The script replaces `django/` entirely. Update the tag and commit above.

## Gaps

Failing tests go in [`../index.toml`](../index.toml) by node id; `tests/conftest.py`
marks them. Add a run's new failures as `unclassified` pending, then print the counts:

```sh
uv run python -m scripts.sync_index --junit junit-vendor-pyturso.xml --target pyturso
uv run python -m scripts.report_index
```

## License

The vendored files are Django's, under Django's BSD 3-clause license:

```text
Copyright (c) Django Software Foundation and individual contributors.
All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

    1. Redistributions of source code must retain the above copyright notice,
       this list of conditions and the following disclaimer.

    2. Redistributions in binary form must reproduce the above copyright
       notice, this list of conditions and the following disclaimer in the
       documentation and/or other materials provided with the distribution.

    3. Neither the name of Django nor the names of its contributors may be used
       to endorse or promote products derived from this software without
       specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```
