"""Apply the gap index to every suite: each indexed test gets its kind's mark."""

import collections.abc
import contextlib
import os
import pathlib
import typing as t

import django.db
import pytest

from tests import gap_index

MARKER_KINDS = {
    f"turso_{kind.removeprefix('turso-').replace('-', '_')}": kind
    for kind in gap_index.KINDS
}

causes_key = pytest.StashKey[list[gap_index.Cause]]()

running_call_key = pytest.StashKey[bool]()

failed_subtests_key = pytest.StashKey[int]()


class WorkerController(t.Protocol):
    config: pytest.Config


# Loaded here so a bad index stops an xdist run before any worker starts.
def pytest_configure(config: pytest.Config) -> None:
    for marker_name, kind in MARKER_KINDS.items():
        config.addinivalue_line(
            "markers",
            f"{marker_name}(name, reason='', issue='', raises='', match=''): "
            f"known {kind} gap, "
            "marked as the gap index marks it",
        )
    with raise_usage_error():
        config.stash[causes_key] = gap_index.load_index(
            pathlib.Path(
                os.environ.get(
                    "DJ_TURSO_INDEX", pathlib.Path(__file__).with_name("index.toml")
                )
            )
        )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    causes = config.stash[causes_key]
    # An xdist worker cannot stop the run cleanly; the controller checks its ids.
    if not hasattr(config, "workerinput"):
        with raise_usage_error():
            check_stale_entries(config, [item.nodeid for item in items], causes)
    # The stdlib sqlite3 control run must pass without the index.
    if os.environ.get("DJ_TURSO_TARGET", "pyturso") == "sqlite":
        causes = []
    causes_by_entry = {entry: cause for cause in causes for entry in cause.tests}
    for item in items:
        for marker in list(item.iter_markers()):
            if marker.name in MARKER_KINDS:
                with raise_usage_error():
                    cause = gap_index.Cause(
                        marker.args[0],
                        MARKER_KINDS[marker.name],
                        (),
                        **marker.kwargs,
                    )
                item.add_marker(get_mark(cause))
        for prefix in gap_index.get_node_id_prefixes(item.nodeid):
            if prefix in causes_by_entry:
                item.add_marker(get_mark(causes_by_entry[prefix]))


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(
    node: WorkerController, ids: collections.abc.Sequence[str]
) -> None:
    with raise_usage_error():
        check_stale_entries(node.config, ids, node.config.stash[causes_key])


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> collections.abc.Generator[None]:
    item.stash[running_call_key] = True
    try:
        return (yield)
    finally:
        item.stash[running_call_key] = False


# An expected failure covers the whole test. A subtest's report, made while the test
# runs, would otherwise count each passing subtest as a strict unexpected pass.
@pytest.hookimpl(wrapper=True, trylast=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> collections.abc.Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    xfail = item.get_closest_marker("xfail")
    if xfail is None or call.when != "call":
        return report
    if item.stash.get(running_call_key, False):
        if call.excinfo is None:
            report.outcome = "skipped"
            report.longrepr = (
                item.location[0],
                item.location[1] or 0,
                "Skipped: passes within a test expected to fail",
            )
        else:
            item.stash[failed_subtests_key] = item.stash.get(failed_subtests_key, 0) + 1
    elif call.excinfo is None and item.stash.get(failed_subtests_key, 0):
        report.outcome = "skipped"
        report.wasxfail = xfail.kwargs["reason"]
    return report


def get_mark(cause: gap_index.Cause) -> pytest.MarkDecorator:
    match cause.kind:
        case "pending":
            return pytest.mark.xfail(strict=True, reason=cause.name)
        case "unsupported":
            return pytest.mark.xfail(
                strict=True, raises=django.db.NotSupportedError, reason=cause.name
            )
        case "turso-limitation":
            return pytest.mark.xfail(
                strict=True,
                raises=pytest.RaisesExc(
                    gap_index.get_exception_class(cause.raises, cause.name),
                    match=cause.match,
                ),
                reason=cause.name,
            )
        case "upstream-bug":
            return pytest.mark.xfail(strict=True, reason=f"{cause.name}: {cause.issue}")
        case _:
            return pytest.mark.skip(reason=cause.reason)


# Only entries in files this run collected whole: a partial run says nothing of
# the rest. --lf drops a file's passing tests from collection, not deselection.
def check_stale_entries(
    config: pytest.Config,
    node_ids: collections.abc.Sequence[str],
    causes: list[gap_index.Cause],
) -> None:
    if config.getoption("lf", default=False):
        return
    narrowed_files = {
        (config.invocation_params.dir / argument.partition("::")[0]).resolve()
        for argument in config.args
        if "::" in argument
    }
    whole_files = {
        (config.rootpath / node_id.partition("::")[0]).resolve() for node_id in node_ids
    } - narrowed_files
    collected = {
        prefix
        for node_id in node_ids
        for prefix in gap_index.get_node_id_prefixes(node_id)
    }
    stale = sorted(
        {
            entry
            for cause in causes
            for entry in cause.tests
            if (config.rootpath / entry.partition("::")[0]).resolve() in whole_files
        }
        - collected
    )
    if stale:
        raise gap_index.StaleEntriesError(stale)


# pytest prints a usage error's message alone and exits with its usage status.
@contextlib.contextmanager
def raise_usage_error() -> collections.abc.Iterator[None]:
    try:
        yield
    except gap_index.InvalidIndexError as exc:
        raise pytest.UsageError(str(exc)) from exc
