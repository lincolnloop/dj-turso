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
