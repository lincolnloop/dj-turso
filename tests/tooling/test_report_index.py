import pathlib
import subprocess
import sys


def test_report_prints_counts_by_kind_and_cause(tmp_path: pathlib.Path) -> None:
    index = tmp_path / "index.toml"
    index.write_text(
        """
        [[cause]]
        name = "Binary missing"
        kind = "pending"
        tests = ["tests/vendor/django/model_fields/test_binaryfield.py"]

        [[cause]]
        name = "missing datetime UDFs"
        kind = "pending"
        tests = [
            "tests/vendor/django/datetimes/tests.py::DateTimesTests",
            "tests/vendor/django/db_functions/datetime/test_extract_trunc.py",
        ]

        [[cause]]
        name = "non-UTC time zone"
        kind = "unsupported"
        tests = ["tests/vendor/django/timezones/tests.py::NewDatabaseTests"]
        """
    )

    completed = subprocess.run(
        [sys.executable, "-m", "scripts.report_index", "--index", index],
        capture_output=True,
        check=True,
        cwd=pathlib.Path(__file__).parents[2],
        text=True,
    )

    assert completed.stdout == (
        "pending 3\n"
        "  2 missing datetime UDFs\n"
        "  1 Binary missing\n"
        "unsupported 1\n"
        "  1 non-UTC time zone\n"
        "upstream-bug 0\n"
        "stdlib-internal 0\n"
    )


def test_report_rejects_invalid_index(tmp_path: pathlib.Path) -> None:
    index = tmp_path / "index.toml"
    index.write_text(
        """
        [[cause]]
        name = "dates"
        kind = "pendng"
        tests = ["tests/vendor/django/datetimes/tests.py"]
        """
    )

    completed = subprocess.run(
        [sys.executable, "-m", "scripts.report_index", "--index", index],
        capture_output=True,
        check=False,
        cwd=pathlib.Path(__file__).parents[2],
        text=True,
    )

    assert completed.returncode == 1
    assert completed.stderr == "cause 'dates': unknown kind 'pendng'\n"
    assert completed.stdout == ""
