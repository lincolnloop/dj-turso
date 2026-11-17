"""Copy the listed Django test apps and support modules, unedited, into the repo."""

import argparse
import pathlib
import shutil
import sys


class MissingSourceEntriesError(Exception):
    def __init__(self, names: list[str]) -> None:
        super().__init__(f"missing in source: {', '.join(names)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=pathlib.Path)
    parser.add_argument(
        "--destination", default="tests/vendor/django", type=pathlib.Path
    )
    parser.add_argument(
        "--app-list", default="tests/vendor/apps.txt", type=pathlib.Path
    )
    arguments = parser.parse_args()
    sections = read_app_list(arguments.app_list)
    try:
        vendor_entries(
            arguments.source,
            arguments.destination,
            sections["apps"] + sections["support"],
        )
    except MissingSourceEntriesError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    sys.stdout.write(
        f"vendored {len(sections['apps'])} apps, "
        f"{len(sections['support'])} support entries\n"
    )
    return 0


def read_app_list(path: pathlib.Path) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    entries: list[str] = []
    for line in path.read_text().splitlines():
        name = line.strip()
        if name.startswith("[") and name.endswith("]"):
            entries = sections.setdefault(name[1:-1], [])
        elif name:
            entries.append(name)
    return sections


def vendor_entries(
    source: pathlib.Path, destination: pathlib.Path, names: list[str]
) -> None:
    missing = [name for name in names if not (source / name).exists()]
    if missing:
        raise MissingSourceEntriesError(missing)
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    for name in names:
        if (source / name).is_dir():
            shutil.copytree(
                source / name,
                destination / name,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
        else:
            shutil.copy2(source / name, destination / name)


if __name__ == "__main__":
    raise SystemExit(main())
