"""Apply the gap index to every suite: each indexed test gets its kind's mark."""

import os
import pathlib

import django.db
import pytest

from tests import gap_index


def pytest_configure(config: pytest.Config) -> None:
    pass


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    causes = gap_index.load_index(
        pathlib.Path(
            os.environ.get(
                "DJ_TURSO_INDEX", pathlib.Path(__file__).with_name("index.toml")
            )
        )
    )
    target = os.environ.get("DJ_TURSO_TARGET", "pyturso")
    causes_by_entry = {
        entry: cause
        for cause in causes
        if target in cause.targets
        for entry in cause.tests
    }
    for item in items:
        for prefix in get_node_id_prefixes(item.nodeid):
            if prefix in causes_by_entry:
                item.add_marker(get_mark(causes_by_entry[prefix]))


def get_mark(cause: gap_index.Cause) -> pytest.MarkDecorator:
    match cause.kind:
        case "pending":
            return pytest.mark.xfail(strict=True, reason=cause.name)
        case "unsupported":
            return pytest.mark.xfail(
                strict=True, raises=django.db.NotSupportedError, reason=cause.name
            )
        case "upstream-bug":
            return pytest.mark.xfail(strict=True, reason=f"{cause.name}: {cause.issue}")
        case _:
            return pytest.mark.skip(reason=cause.reason)


# The module, each enclosing class, then the test itself.
def get_node_id_prefixes(node_id: str) -> list[str]:
    parts = node_id.partition("[")[0].split("::")
    return [*("::".join(parts[:end]) for end in range(1, len(parts))), node_id]
