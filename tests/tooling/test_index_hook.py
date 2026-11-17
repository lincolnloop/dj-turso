import pytest


def test_unsupported_entry_passes_only_on_not_supported_error(
    index_pytester: pytest.Pytester,
) -> None:
    index_pytester.makepyfile(
        test_sample="""
        import django.db

        def test_raises_not_supported():
            raise django.db.NotSupportedError("no")

        def test_raises_other():
            raise ValueError("other")
        """
    )
    index_pytester.makefile(
        ".toml",
        index="""
        [[cause]]
        name = "no tz"
        kind = "unsupported"
        targets = ["pyturso"]
        reason = "UTC only"
        tests = [
            "test_sample.py::test_raises_not_supported",
            "test_sample.py::test_raises_other",
        ]
        """,
    )

    result = index_pytester.runpytest("-p", "no:django")

    result.assert_outcomes(failed=1, xfailed=1)


def test_pending_entry_marks_failing_test_expected_failure(
    index_pytester: pytest.Pytester,
) -> None:
    index_pytester.makepyfile(
        test_sample="""
        def test_fails():
            raise AssertionError
        """
    )
    index_pytester.makefile(
        ".toml",
        index="""
        [[cause]]
        name = "missing datetime UDFs"
        kind = "pending"
        targets = ["pyturso"]
        tests = ["test_sample.py::test_fails"]
        """,
    )

    recorder = index_pytester.inline_run("-p", "no:django")

    report = recorder.matchreport("test_fails", when="call")
    assert report.outcome == "skipped"
    assert report.wasxfail == "missing datetime UDFs"


def test_pending_entry_fails_run_when_test_passes(
    index_pytester: pytest.Pytester,
) -> None:
    index_pytester.makepyfile(
        test_sample="""
        def test_passes():
            pass
        """
    )
    index_pytester.makefile(
        ".toml",
        index="""
        [[cause]]
        name = "missing datetime UDFs"
        kind = "pending"
        targets = ["pyturso"]
        tests = ["test_sample.py::test_passes"]
        """,
    )

    result = index_pytester.runpytest("-p", "no:django")

    result.assert_outcomes(failed=1)


def test_upstream_bug_entry_reports_issue_with_expected_failure(
    index_pytester: pytest.Pytester,
) -> None:
    index_pytester.makepyfile(
        test_sample="""
        def test_fails():
            raise AssertionError
        """
    )
    index_pytester.makefile(
        ".toml",
        index="""
        [[cause]]
        name = "HAVING subquery wrong rows"
        kind = "upstream-bug"
        targets = ["pyturso"]
        issue = "TBD"
        tests = ["test_sample.py::test_fails"]
        """,
    )

    recorder = index_pytester.inline_run("-p", "no:django")

    report = recorder.matchreport("test_fails", when="call")
    assert report.outcome == "skipped"
    assert report.wasxfail == "HAVING subquery wrong rows: TBD"


def test_upstream_bug_entry_fails_run_when_test_passes(
    index_pytester: pytest.Pytester,
) -> None:
    index_pytester.makepyfile(
        test_sample="""
        def test_passes():
            pass
        """
    )
    index_pytester.makefile(
        ".toml",
        index="""
        [[cause]]
        name = "HAVING subquery wrong rows"
        kind = "upstream-bug"
        targets = ["pyturso"]
        issue = "TBD"
        tests = ["test_sample.py::test_passes"]
        """,
    )

    result = index_pytester.runpytest("-p", "no:django")

    result.assert_outcomes(failed=1)


def test_stdlib_internal_entry_skips_with_reason(
    index_pytester: pytest.Pytester,
) -> None:
    index_pytester.makepyfile(
        test_sample="""
        def test_uses_getlimit():
            raise AssertionError
        """
    )
    index_pytester.makefile(
        ".toml",
        index="""
        [[cause]]
        name = "sqlite3 limits"
        kind = "stdlib-internal"
        targets = ["pyturso"]
        reason = "asserts on sqlite3.Connection.getlimit"
        tests = ["test_sample.py::test_uses_getlimit"]
        """,
    )

    recorder = index_pytester.inline_run("-p", "no:django")

    report = recorder.matchreport("test_uses_getlimit", when="setup")
    assert report.outcome == "skipped"
    assert report.longrepr == (
        str(index_pytester.path / "test_sample.py"),
        1,
        "Skipped: asserts on sqlite3.Connection.getlimit",
    )


def test_entry_for_other_target_leaves_test_unmarked(
    index_pytester: pytest.Pytester,
) -> None:
    index_pytester.makepyfile(
        test_sample="""
        def test_fails():
            raise AssertionError
        """
    )
    index_pytester.makefile(
        ".toml",
        index="""
        [[cause]]
        name = "remote exception class"
        kind = "pending"
        targets = ["sync-server"]
        tests = ["test_sample.py::test_fails"]
        """,
    )

    result = index_pytester.runpytest("-p", "no:django")

    result.assert_outcomes(failed=1)


def test_sqlite_target_leaves_indexed_test_unmarked(
    index_pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DJ_TURSO_TARGET", "sqlite")
    index_pytester.makepyfile(
        test_sample="""
        def test_fails():
            raise AssertionError
        """
    )
    index_pytester.makefile(
        ".toml",
        index="""
        [[cause]]
        name = "missing datetime UDFs"
        kind = "pending"
        targets = ["pyturso", "sync-server"]
        tests = ["test_sample.py::test_fails"]
        """,
    )

    result = index_pytester.runpytest("-p", "no:django")

    result.assert_outcomes(failed=1)


def test_class_entry_marks_every_method(index_pytester: pytest.Pytester) -> None:
    index_pytester.makepyfile(
        test_sample="""
        class TestDates:
            def test_first(self):
                raise AssertionError

            def test_second(self):
                raise AssertionError

        class TestOther:
            def test_third(self):
                raise AssertionError
        """
    )
    index_pytester.makefile(
        ".toml",
        index="""
        [[cause]]
        name = "missing datetime UDFs"
        kind = "pending"
        targets = ["pyturso"]
        tests = ["test_sample.py::TestDates"]
        """,
    )

    result = index_pytester.runpytest("-p", "no:django")

    result.assert_outcomes(failed=1, xfailed=2)


def test_module_entry_marks_every_test_in_module(
    index_pytester: pytest.Pytester,
) -> None:
    index_pytester.makepyfile(
        test_sample="""
        def test_first():
            raise AssertionError

        class TestDates:
            def test_second(self):
                raise AssertionError
        """,
        test_sample_other="""
        def test_third():
            raise AssertionError
        """,
    )
    index_pytester.makefile(
        ".toml",
        index="""
        [[cause]]
        name = "missing datetime UDFs"
        kind = "pending"
        targets = ["pyturso"]
        tests = ["test_sample.py"]
        """,
    )

    result = index_pytester.runpytest("-p", "no:django")

    result.assert_outcomes(failed=1, xfailed=2)
