"""Print the gap index's progress report: test counts by target, kind and cause."""

import argparse
import collections
import pathlib
import sys

from tests import gap_index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", default="tests/index.toml", type=pathlib.Path)
    arguments = parser.parse_args()
    try:
        causes = gap_index.load_index(arguments.index)
    except gap_index.InvalidIndexError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    for target in gap_index.TARGETS:
        sys.stdout.write(f"target {target}\n")
        for kind in gap_index.KINDS:
            counts = collections.Counter[str]()
            for cause in causes:
                if cause.kind == kind and target in cause.targets:
                    counts[cause.name] += len(cause.tests)
            sys.stdout.write(f"  {kind} {counts.total()}\n")
            for name, count in sorted(counts.items(), key=lambda pair: -pair[1]):
                sys.stdout.write(f"    {count} {name}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
