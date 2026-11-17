import pathlib
import subprocess
import sys

import pytest


def test_sync_appends_unindexed_failures_as_unclassified_pending(
    pytester: pytest.Pytester,
) -> None:
    (pytester.path / "suite").mkdir()
    (pytester.path / "suite" / "test_sample.py").write_text(
        "import pytest\n"
        "\n"
        "@pytest.fixture\n"
        "def broken():\n"
        "    raise RuntimeError\n"
        "\n"
        "def test_indexed():\n"
        "    raise AssertionError\n"
        "\n"
        "def test_other_target():\n"
        "    raise AssertionError\n"
        "\n"
        "def test_setup_error(broken):\n"
        "    pass\n"
        "\n"
        "def test_passes():\n"
        "    pass\n"
        "\n"
        "class TestDates:\n"
        "    def test_covered(self):\n"
        "        raise AssertionError\n"
        "\n"
        "class TestTimes:\n"
        '    @pytest.mark.parametrize("value", ["a.b", "c"])\n'
        "    def test_param(self, value):\n"
        "        raise AssertionError\n"
        "\n"
        "    def test_skipped(self):\n"
        '        pytest.skip("not here")\n'
    )
    pytester.runpytest("-p", "no:django", "--junitxml=junit.xml")
    index_text = (
        "[[cause]]\n"
        'kind = "pending"\n'
        'name = "missing datetime UDFs"\n'
        'targets = ["pyturso"]\n'
        "tests = [\n"
        '  "suite/test_sample.py::TestDates",\n'
        '  "suite/test_sample.py::test_indexed",\n'
        "]\n"
        "\n"
        "[[cause]]\n"
        'kind = "pending"\n'
        'name = "remote exception class"\n'
        'targets = ["sync-server"]\n'
        'tests = ["suite/test_sample.py::test_other_target"]\n'
    )
    (pytester.path / "index.toml").write_text(index_text)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.sync_index",
            "--junit",
            pytester.path / "junit.xml",
            "--target",
            "pyturso",
            "--index",
            pytester.path / "index.toml",
            "--root",
            pytester.path,
        ],
        capture_output=True,
        check=True,
        cwd=pathlib.Path(__file__).parents[2],
        text=True,
    )

    assert completed.stdout == "added 4\n"
    assert (pytester.path / "index.toml").read_text() == (
        index_text + "\n"
        "[[cause]]\n"
        'kind = "pending"\n'
        'name = "unclassified"\n'
        'targets = ["pyturso"]\n'
        "tests = [\n"
        '  "suite/test_sample.py::TestTimes::test_param[a.b]",\n'
        '  "suite/test_sample.py::TestTimes::test_param[c]",\n'
        '  "suite/test_sample.py::test_other_target",\n'
        '  "suite/test_sample.py::test_setup_error",\n'
        "]\n"
    )


def test_sync_leaves_index_alone_without_new_failures(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        test_sample="""
        def test_indexed():
            raise AssertionError
        """
    )
    pytester.runpytest("-p", "no:django", "--junitxml=junit.xml")
    index_text = (
        "[[cause]]\n"
        'kind = "pending"\n'
        'name = "missing datetime UDFs"\n'
        'targets = ["pyturso"]\n'
        'tests = ["test_sample.py::test_indexed"]\n'
    )
    (pytester.path / "index.toml").write_text(index_text)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.sync_index",
            "--junit",
            pytester.path / "junit.xml",
            "--target",
            "pyturso",
            "--index",
            pytester.path / "index.toml",
            "--root",
            pytester.path,
        ],
        capture_output=True,
        check=True,
        cwd=pathlib.Path(__file__).parents[2],
        text=True,
    )

    assert completed.stdout == "added 0\n"
    assert (pytester.path / "index.toml").read_text() == index_text


def test_sync_rejects_collection_errors(pytester: pytest.Pytester) -> None:
    (pytester.path / "suite").mkdir()
    (pytester.path / "suite" / "test_broken.py").write_text("import nope\n")
    pytester.runpytest("-p", "no:django", "--junitxml=junit.xml")
    (pytester.path / "index.toml").write_text("")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.sync_index",
            "--junit",
            pytester.path / "junit.xml",
            "--target",
            "pyturso",
            "--index",
            pytester.path / "index.toml",
            "--root",
            pytester.path,
        ],
        capture_output=True,
        check=False,
        cwd=pathlib.Path(__file__).parents[2],
        text=True,
    )

    assert completed.returncode == 1
    assert (
        completed.stderr == "collection errors cannot be indexed: suite.test_broken\n"
    )
    assert completed.stdout == ""
    assert (pytester.path / "index.toml").read_text() == ""


def test_sync_rejects_failure_without_test_module(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        test_sample="""
        def test_fails():
            raise AssertionError
        """
    )
    pytester.runpytest("-p", "no:django", "--junitxml=junit.xml")
    (pytester.path / "test_sample.py").unlink()
    (pytester.path / "index.toml").write_text("")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.sync_index",
            "--junit",
            pytester.path / "junit.xml",
            "--target",
            "pyturso",
            "--index",
            pytester.path / "index.toml",
            "--root",
            pytester.path,
        ],
        capture_output=True,
        check=False,
        cwd=pathlib.Path(__file__).parents[2],
        text=True,
    )

    assert completed.returncode == 1
    assert completed.stderr == f"no test module under {pytester.path} for test_sample\n"
    assert completed.stdout == ""
    assert (pytester.path / "index.toml").read_text() == ""
