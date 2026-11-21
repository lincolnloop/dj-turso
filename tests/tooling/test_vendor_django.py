import pathlib
import subprocess
import sys


def test_vendor_script_copies_listed_entries_and_removes_stale(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "LICENSE").write_text("Copyright (c) Django Software Foundation\n")
    source = tmp_path / "source"
    (source / "lookup" / "__pycache__").mkdir(parents=True)
    (source / "lookup" / "__pycache__" / "tests.cpython-315.pyc").write_bytes(b"")
    (source / "lookup" / "tests.py").write_text("LOOKUP = 1\n")
    (source / "unlisted").mkdir()
    (source / "urls.py").write_text("urlpatterns = []\n")
    destination = tmp_path / "destination"
    (destination / "stale").mkdir(parents=True)
    app_list = tmp_path / "apps.txt"
    app_list.write_text("[apps]\nlookup\n\n[support]\nurls.py\n")

    completed = subprocess.run(
        [
            sys.executable,
            pathlib.Path(__file__).parents[2] / "scripts" / "vendor_django.py",
            "--source",
            source,
            "--destination",
            destination,
            "--app-list",
            app_list,
        ],
        capture_output=True,
        check=True,
        text=True,
    )

    assert completed.stdout == "vendored 1 apps, 1 support entries\n"
    assert sorted(
        path.relative_to(destination).as_posix() for path in destination.rglob("*")
    ) == ["LICENSE", "lookup", "lookup/tests.py", "urls.py"]
    assert (destination / "LICENSE").read_text() == (
        "Copyright (c) Django Software Foundation\n"
    )
    assert (destination / "lookup" / "tests.py").read_text() == "LOOKUP = 1\n"
    assert (destination / "urls.py").read_text() == "urlpatterns = []\n"


def test_vendor_script_rejects_missing_entry_before_touching_destination(
    tmp_path: pathlib.Path,
) -> None:
    source = tmp_path / "source"
    (source / "lookup").mkdir(parents=True)
    destination = tmp_path / "destination"
    (destination / "stale").mkdir(parents=True)
    app_list = tmp_path / "apps.txt"
    app_list.write_text("[apps]\nlookup\nnope\n[support]\nmissing.py\n")

    completed = subprocess.run(
        [
            sys.executable,
            pathlib.Path(__file__).parents[2] / "scripts" / "vendor_django.py",
            "--source",
            source,
            "--destination",
            destination,
            "--app-list",
            app_list,
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 1
    assert completed.stderr == "missing in source: nope, missing.py, ../LICENSE\n"
    assert completed.stdout == ""
    assert sorted(
        path.relative_to(destination).as_posix() for path in destination.rglob("*")
    ) == ["stale"]


def test_vendor_script_creates_absent_destination(tmp_path: pathlib.Path) -> None:
    (tmp_path / "LICENSE").write_text("Copyright (c) Django Software Foundation\n")
    source = tmp_path / "source"
    source.mkdir()
    (source / "urls.py").write_text("urlpatterns = []\n")
    destination = tmp_path / "vendor" / "django"
    app_list = tmp_path / "apps.txt"
    app_list.write_text("[apps]\n[support]\nurls.py\n")

    completed = subprocess.run(
        [
            sys.executable,
            pathlib.Path(__file__).parents[2] / "scripts" / "vendor_django.py",
            "--source",
            source,
            "--destination",
            destination,
            "--app-list",
            app_list,
        ],
        capture_output=True,
        check=True,
        text=True,
    )

    assert completed.stdout == "vendored 0 apps, 1 support entries\n"
    assert sorted(
        path.relative_to(destination).as_posix() for path in destination.rglob("*")
    ) == ["LICENSE", "urls.py"]
