"""Add a junit report's failures missing from the gap index as unclassified pending.

Node ids come back from junit class names by finding the test module under --root,
the rootdir of the run that wrote the report.
"""

import argparse
import json
import pathlib
import sys

from defusedxml import ElementTree

from tests import gap_index


class SyncIndexError(Exception):
    pass


class CollectionErrorsError(SyncIndexError):
    def __init__(self, names: list[str]) -> None:
        super().__init__(f"collection errors cannot be indexed: {', '.join(names)}")


class UnresolvableTestError(SyncIndexError):
    def __init__(self, class_name: str, root: pathlib.Path) -> None:
        super().__init__(f"no test module under {root} for {class_name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", required=True, type=pathlib.Path)
    parser.add_argument("--index", default="tests/index.toml", type=pathlib.Path)
    parser.add_argument("--root", default=".", type=pathlib.Path)
    arguments = parser.parse_args()
    try:
        causes = gap_index.load_index(arguments.index)
        failed = read_failed_node_ids(arguments.junit, arguments.root)
    except (gap_index.InvalidIndexError, SyncIndexError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    indexed = {entry for cause in causes for entry in cause.tests}
    added = sorted(
        node_id
        for node_id in failed
        if indexed.isdisjoint(gap_index.get_node_id_prefixes(node_id))
    )
    if added:
        append_unclassified_cause(arguments.index, added)
    sys.stdout.write(f"added {len(added)}\n")
    return 0


def read_failed_node_ids(junit: pathlib.Path, root: pathlib.Path) -> set[str]:
    node_ids = set()
    collection_errors = []
    for test_case in ElementTree.parse(junit).iter("testcase"):
        if test_case.find("failure") is None and test_case.find("error") is None:
            continue
        class_name = test_case.get("classname", "")
        if class_name:
            node_ids.add(get_node_id(class_name, test_case.get("name", ""), root))
        else:
            collection_errors.append(test_case.get("name", ""))
    if collection_errors:
        raise CollectionErrorsError(collection_errors)
    return node_ids


# pytest's junit class name is the node id's module path, dotted, then its classes.
def get_node_id(class_name: str, name: str, root: pathlib.Path) -> str:
    parts = class_name.split(".")
    for end in range(len(parts), 0, -1):
        module = "/".join(parts[:end]) + ".py"
        if (root / module).is_file():
            return "::".join([module, *parts[end:], name])
    raise UnresolvableTestError(class_name, root)


# Formatted as the TOML pre-commit hook formats it, so a sync leaves no diff to it.
def append_unclassified_cause(index: pathlib.Path, node_ids: list[str]) -> None:
    text = index.read_text()
    lines = [
        "[[cause]]",
        'kind = "pending"',
        'name = "unclassified"',
        "tests = [",
        *(f"  {json.dumps(node_id)}," for node_id in node_ids),
        "]",
    ]
    separator = "\n" if text else ""
    index.write_text(text + separator + "\n".join(lines) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
