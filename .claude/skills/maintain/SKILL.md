---
name: maintain
description:
  Use when doing a dj-turso maintenance pass - checking what changed upstream in Django,
  pyturso, turso-serverless, Turso or django-stubs, re-verifying the assumptions in
  tests/assumptions.toml, checking tracked upstream bugs in tests/index.toml, or
  deciding whether a dependency bump is safe.
---

# dj-turso maintenance pass

One run leaves two things: a local `maintain/<date>` branch holding only mechanical
fixes, and a report in chat. Everything that needs judgment goes in the report, not the
branch.

Never push, open a pull request or issue, or post anything upstream. Never edit
`dj_turso/`, bump a dependency version (Renovate owns bumps) or change
`pyproject.toml`/`uv.lock`. Read-only `gh` and web lookups are fine.

## Steps

Do every step. A step you cannot finish goes in the report's "Not checked" list with the
reason.

1. **Branch.** The tree must be clean. Then:

   ```sh
   /usr/bin/git fetch origin
   /usr/bin/git checkout -b maintain/$(date +%F) origin/main
   ```

2. **Upstream versions.** For Django (6.1.x), pyturso, turso-serverless and
   django-stubs, find the version locked in `uv.lock`, the latest stable release and the
   newest pre-release (PyPI JSON or `gh release list`). Read the release notes or the
   diff between the locked and latest versions for the areas that the register's `where`
   files touch.

3. **Tracked upstream bugs.** For every `[[cause]]` with `kind = "upstream-bug"` in
   `tests/index.toml`, look up its `issue` URL:
   - issue: `gh issue view <url> --json state,closedAt,closedByPullRequestsReferences`
   - pull request: `gh pr view <url> --json state,mergedAt,mergeCommit`
   - Released? For a merged fix, check whether the latest stable tag contains it:
     `gh api repos/tursodatabase/turso/compare/<tag>...<merge sha> --jq .status`
     (`behind` or `identical` means the tag contains it).

4. **Suites.** Run the full gate, every tox environment, one at a time, saving the
   output to a file and reading its summary:

   ```sh
   uvx --with tox-uv tox > <scratch>/tox.log 2>&1
   ```

   The gap index applies to `vendor-pyturso`. A strict xfail that passes shows there as
   `FAILED ... [XPASS(strict)]`: the upstream fix landed. Cloud tests skip without
   `TURSO_API_TOKEN`, `TURSO_ORG` and `TURSO_TEST_GROUP`; list them as skipped.

5. **Register.** Walk every `[[assumption]]` in `tests/assumptions.toml`:
   - `test` entries: a failing test means the fact changed. Decide from the failure
     which way:
     - lost, so dj-turso breaks
     - gained, so the workaround in `where` can go Give `file:line` for the code
       involved.
   - `manual` entries: re-check every one against its `evidence`. Each one ends up as
     one of:
     - confirmed
     - changed (say what changed)
     - not checkable here (say why, for example it needs Turso Cloud)

6. **Pre-release probe.** If step 2 found a newer pre-release of pyturso or
   turso-serverless, install it in a throwaway virtualenv outside the repo's `.venv` and
   run `tests/core` against it. Leave the repo's environment on the locked versions.

7. **Mechanical fixes.** Only these, one commit each, with a conventional subject:
   - **XPASS in the index:** remove that node id from its cause's `tests`, and remove
     the `[[cause]]` when its list is then empty. Make no other change to the cause.
   - **Re-vendored Django tests:** when `uv.lock` holds a newer Django 6.1.x than
     `tests/vendor/VENDORED.md`'s tag, re-vendor them per that file.
   - **Re-dated `verified`:** set `verified` to today and the version checked, only on
     manual entries step 5 confirmed.

   After each commit, re-run the environments it affects; the branch stays green.

8. **Report.** Give these sections, in order:
   1. **Branch:** the name, and each commit with its subject.
   2. **Upstream versions:** a table of dependency, locked, latest stable, newest
      pre-release, and what changed in the areas we touch.
   3. **Tracked upstream bugs:** a table of cause, URL, state, fix released in, and the
      action taken or needed.
   4. **Suites:** each tox environment and its result, plus what was skipped.
   5. **Register:** facts lost (breakage) and facts gained (deletable workarounds), each
      with `file:line`, then each manual entry and its outcome.
   6. **Pre-release probe:** what failed, grouped into facts gained (cleanup) and
      behavior changes (work needed).
   7. **Decisions needed:** everything left for the maintainer, most important first.
   8. **Not checked:** each step left undone, and why.

   Then offer a revdiff of the branch against `origin/main`.
