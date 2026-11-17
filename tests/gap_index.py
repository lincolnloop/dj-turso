"""The index of known gaps: which tests fail on which Turso target, and why."""

import dataclasses
import pathlib
import tomllib


@dataclasses.dataclass(frozen=True)
class Cause:
    name: str
    kind: str
    targets: tuple[str, ...]
    reason: str
    issue: str
    tests: tuple[str, ...]


def load_index(path: pathlib.Path) -> list[Cause]:
    document = tomllib.loads(path.read_text())
    return [
        Cause(
            name=entry["name"],
            kind=entry["kind"],
            targets=tuple(entry["targets"]),
            reason=entry.get("reason", ""),
            issue=entry.get("issue", ""),
            tests=tuple(entry["tests"]),
        )
        for entry in document.get("cause", [])
    ]
