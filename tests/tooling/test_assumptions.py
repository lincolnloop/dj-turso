import pathlib
import subprocess
import sys
import tomllib

REPO_ROOT = pathlib.Path(__file__).parents[2]


def test_assumption_register_tests_are_collected() -> None:
    register = tomllib.loads((REPO_ROOT / "tests" / "assumptions.toml").read_text())
    node_ids = sorted(
        assumption["test"]
        for assumption in register["assumption"]
        if "test" in assumption
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "--quiet",
            "--config-file=tests/core/pytest.toml",
            "--rootdir=.",
            "--override-ini=addopts=",
            "-p",
            "no:cacheprovider",
            *node_ids,
        ],
        capture_output=True,
        check=False,
        cwd=REPO_ROOT,
        text=True,
    )

    assert completed.stderr == ""
    assert (
        sorted(line for line in completed.stdout.splitlines() if "::" in line)
        == node_ids
    )


def test_assumption_register_entries_are_tested_or_checked_by_hand() -> None:
    register = tomllib.loads((REPO_ROOT / "tests" / "assumptions.toml").read_text())

    shapes = {tuple(sorted(assumption)) for assumption in register["assumption"]}

    assert shapes == {
        ("evidence", "manual", "name", "verified", "where"),
        ("evidence", "name", "test", "where"),
    }


def test_assumption_register_where_paths_exist() -> None:
    register = tomllib.loads((REPO_ROOT / "tests" / "assumptions.toml").read_text())
    paths = sorted(
        {path for assumption in register["assumption"] for path in assumption["where"]}
    )

    assert {path: (REPO_ROOT / path).is_file() for path in paths} == dict.fromkeys(
        paths, True
    )
