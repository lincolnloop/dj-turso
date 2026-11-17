"""The index of known gaps: which tests fail on Turso, and why."""

import dataclasses
import pathlib
import pkgutil
import tomllib
import typing as t

UNFILED_ISSUE = "unfiled"

KINDS = (
    "pending",
    "unsupported",
    "turso-limitation",
    "upstream-bug",
    "stdlib-internal",
)


class InvalidIndexError(Exception):
    pass


class UnknownTablesError(InvalidIndexError):
    def __init__(self, names: list[str]) -> None:
        super().__init__(f"unknown index tables: {', '.join(names)}")


class UnknownFieldsError(InvalidIndexError):
    def __init__(self, number: int, names: list[str]) -> None:
        super().__init__(f"cause #{number}: unknown fields: {', '.join(names)}")


class MissingFieldsError(InvalidIndexError):
    def __init__(self, number: int, names: list[str]) -> None:
        super().__init__(f"cause #{number}: missing fields: {', '.join(names)}")


class UnknownKindError(InvalidIndexError):
    def __init__(self, cause: str, kind: str) -> None:
        super().__init__(f"cause {cause!r}: unknown kind {kind!r}")


class MissingIssueError(InvalidIndexError):
    def __init__(self, cause: str) -> None:
        super().__init__(f"cause {cause!r}: upstream-bug needs an issue")


class InvalidIssueError(InvalidIndexError):
    def __init__(self, cause: str, issue: str) -> None:
        super().__init__(
            f"cause {cause!r}: upstream-bug issue must be a URL or "
            f"{UNFILED_ISSUE!r}, not {issue!r}"
        )


class MissingReasonError(InvalidIndexError):
    def __init__(self, cause: str) -> None:
        super().__init__(f"cause {cause!r}: stdlib-internal needs a reason")


class MissingExpectedErrorError(InvalidIndexError):
    def __init__(self, cause: str) -> None:
        super().__init__(f"cause {cause!r}: turso-limitation needs raises and match")


class UnknownRaisesError(InvalidIndexError):
    def __init__(self, cause: str, raises: str) -> None:
        super().__init__(f"cause {cause!r}: cannot import raises {raises!r}")


class DuplicateTestError(InvalidIndexError):
    def __init__(self, test: str) -> None:
        super().__init__(f"{test} listed more than once")


class StaleEntriesError(InvalidIndexError):
    def __init__(self, entries: list[str]) -> None:
        super().__init__(f"index entries match no collected test: {', '.join(entries)}")


@dataclasses.dataclass(frozen=True)
class Cause:
    name: str
    kind: str
    tests: tuple[str, ...]
    reason: str = ""
    issue: str = ""
    raises: str = ""
    match: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise UnknownKindError(self.name, self.kind)
        if self.kind == "upstream-bug":
            if not self.issue:
                raise MissingIssueError(self.name)
            if self.issue != UNFILED_ISSUE and not self.issue.startswith("https://"):
                raise InvalidIssueError(self.name, self.issue)
        if self.kind == "stdlib-internal" and not self.reason:
            raise MissingReasonError(self.name)
        if self.kind == "turso-limitation":
            if not (self.raises and self.match):
                raise MissingExpectedErrorError(self.name)
            get_exception_class(self.raises, self.name)


def load_index(path: pathlib.Path) -> list[Cause]:
    document = tomllib.loads(path.read_text())
    unknown_tables = sorted(document.keys() - {"cause"})
    if unknown_tables:
        raise UnknownTablesError(unknown_tables)
    causes = [
        read_cause(number, entry)
        for number, entry in enumerate(document.get("cause", []), start=1)
    ]
    check_duplicate_tests(causes)
    return causes


def read_cause(number: int, entry: dict[str, t.Any]) -> Cause:
    fields = {field.name: field for field in dataclasses.fields(Cause)}
    unknown = sorted(entry.keys() - fields.keys())
    if unknown:
        raise UnknownFieldsError(number, unknown)
    missing = [
        name
        for name, field in fields.items()
        if name not in entry and field.default is dataclasses.MISSING
    ]
    if missing:
        raise MissingFieldsError(number, missing)
    return Cause(**entry | {"tests": tuple(entry["tests"])})


def check_duplicate_tests(causes: list[Cause]) -> None:
    seen: set[str] = set()
    for cause in causes:
        for test in cause.tests:
            if test in seen:
                raise DuplicateTestError(test)
            seen.add(test)


def get_exception_class(raises: str, cause: str) -> type[BaseException]:
    try:
        exception_class: type[BaseException] = pkgutil.resolve_name(raises)
    except (AttributeError, ImportError, ValueError) as exc:
        raise UnknownRaisesError(cause, raises) from exc
    return exception_class


# The module, each enclosing class, then the test itself.
def get_node_id_prefixes(node_id: str) -> list[str]:
    parts = node_id.partition("[")[0].split("::")
    return [*("::".join(parts[:end]) for end in range(1, len(parts))), node_id]
