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


@pytest.mark.parametrize(
    ("index", "message"),
    [
        pytest.param(
            """
            [[cause]]
            name = "dates"
            kind = "pendng"
            targets = ["pyturso"]
            tests = ["test_sample.py::test_fails"]
            """,
            "cause 'dates': unknown kind 'pendng'",
            id="unknown-kind",
        ),
        pytest.param(
            """
            [[cause]]
            name = "dates"
            kind = "pending"
            targets = ["pyturso", "local"]
            tests = ["test_sample.py::test_fails"]
            """,
            "cause 'dates': unknown target 'local'",
            id="unknown-target",
        ),
        pytest.param(
            """
            [[cause]]
            name = "having"
            kind = "upstream-bug"
            targets = ["pyturso"]
            tests = ["test_sample.py::test_fails"]
            """,
            "cause 'having': upstream-bug needs an issue",
            id="upstream-bug-without-issue",
        ),
        pytest.param(
            """
            [[cause]]
            name = "limits"
            kind = "stdlib-internal"
            targets = ["pyturso"]
            tests = ["test_sample.py::test_fails"]
            """,
            "cause 'limits': stdlib-internal needs a reason",
            id="stdlib-internal-without-reason",
        ),
        pytest.param(
            """
            [[cause]]
            name = "dates"
            kind = "pending"
            targets = ["pyturso"]
            tests = ["test_sample.py::test_fails"]

            [[cause]]
            name = "binary"
            kind = "pending"
            targets = ["sync-server", "pyturso"]
            tests = ["test_sample.py::test_fails"]
            """,
            "test_sample.py::test_fails listed more than once for target pyturso",
            id="duplicate-test",
        ),
        pytest.param(
            """
            [[cause]]
            name = "dates"
            kind = "pending"
            target = ["pyturso"]
            tests = ["test_sample.py::test_fails"]
            """,
            "cause #1: unknown fields: target",
            id="unknown-field",
        ),
        pytest.param(
            """
            [[cause]]
            name = "dates"
            kind = "pending"
            tests = ["test_sample.py::test_fails"]

            [[cause]]
            reason = "no name"
            """,
            "cause #1: missing fields: targets",
            id="missing-field",
        ),
        pytest.param(
            """
            [[causes]]
            name = "dates"
            """,
            "unknown index tables: causes",
            id="unknown-table",
        ),
    ],
)
def test_invalid_index_stops_run_with_usage_error(
    index: str, index_pytester: pytest.Pytester, message: str
) -> None:
    index_pytester.makepyfile(
        test_sample="""
        def test_fails():
            raise AssertionError
        """
    )
    index_pytester.makefile(".toml", index=index)

    result = index_pytester.runpytest("-p", "no:django")

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert result.stderr.lines == [f"ERROR: {message}", ""]


def test_turso_markers_expand_like_index_kinds(
    index_pytester: pytest.Pytester,
) -> None:
    index_pytester.makepyfile(
        test_sample="""
        import django.db
        import pytest

        @pytest.mark.turso_pending("missing datetime UDFs")
        def test_pending():
            raise AssertionError

        @pytest.mark.turso_unsupported("non-UTC time zone")
        def test_unsupported():
            raise django.db.NotSupportedError("UTC only")

        @pytest.mark.turso_upstream_bug("HAVING subquery wrong rows", issue="TBD")
        def test_upstream_bug():
            raise AssertionError

        @pytest.mark.turso_stdlib_internal("sqlite3 limits", reason="getlimit")
        def test_stdlib_internal():
            raise AssertionError
        """
    )
    index_pytester.makefile(".toml", index="")

    result = index_pytester.runpytest("-p", "no:django", "--strict-markers")

    result.assert_outcomes(skipped=1, xfailed=3)


def test_turso_unsupported_marker_fails_on_other_errors(
    index_pytester: pytest.Pytester,
) -> None:
    index_pytester.makepyfile(
        test_sample="""
        import pytest

        @pytest.mark.turso_unsupported("non-UTC time zone")
        def test_unsupported():
            raise ValueError("other")
        """
    )
    index_pytester.makefile(".toml", index="")

    result = index_pytester.runpytest("-p", "no:django", "--strict-markers")

    result.assert_outcomes(failed=1)


def test_turso_marker_without_required_argument_stops_run(
    index_pytester: pytest.Pytester,
) -> None:
    index_pytester.makepyfile(
        test_sample="""
        import pytest

        @pytest.mark.turso_upstream_bug("HAVING subquery wrong rows")
        def test_upstream_bug():
            raise AssertionError
        """
    )
    index_pytester.makefile(".toml", index="")

    result = index_pytester.runpytest("-p", "no:django", "--strict-markers")

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert result.stderr.lines == [
        "ERROR: cause 'HAVING subquery wrong rows': upstream-bug needs an issue",
        "",
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param([], id="serial"),
        pytest.param(["-n", "2"], id="xdist"),
    ],
)
def test_entry_matching_no_collected_test_stops_run(
    arguments: list[str], index_pytester: pytest.Pytester
) -> None:
    index_pytester.makepyfile(
        test_sample="""
        class TestDates:
            def test_kept(self):
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
        tests = ["test_sample.py::TestDates::test_kept", "test_sample.py::test_gone"]

        [[cause]]
        name = "remote exception class"
        kind = "pending"
        targets = ["sync-server"]
        tests = ["test_sample.py::TestGone"]
        """,
    )

    result = index_pytester.runpytest("-p", "no:django", *arguments)

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert result.stderr.lines == [
        (
            "ERROR: index entries match no collected test: "
            "test_sample.py::TestGone, test_sample.py::test_gone"
        ),
        "",
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param(["test_sample.py"], id="file-not-collected"),
        pytest.param(["test_other.py::test_kept"], id="file-narrowed-to-node-id"),
        pytest.param(["-k", "test_kept"], id="keyword-deselected"),
    ],
)
def test_entry_outside_partial_run_is_not_stale(
    arguments: list[str], index_pytester: pytest.Pytester
) -> None:
    index_pytester.makepyfile(
        test_sample="""
        def test_kept():
            pass
        """,
        test_other="""
        def test_kept():
            pass

        def test_indexed():
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
        tests = ["test_other.py::test_indexed"]
        """,
    )

    result = index_pytester.runpytest("-p", "no:django", *arguments)

    assert result.ret == pytest.ExitCode.OK


def test_entry_outside_last_failed_rerun_is_not_stale(
    index_pytester: pytest.Pytester,
) -> None:
    index_pytester.makepyfile(
        test_sample="""
        def test_fails():
            raise AssertionError

        def test_indexed():
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
        tests = ["test_sample.py::test_indexed"]
        """,
    )
    index_pytester.runpytest("-p", "no:django")

    result = index_pytester.runpytest("-p", "no:django", "--lf")

    result.assert_outcomes(failed=1)


def test_class_entry_covers_unittest_class_setup_error(
    index_pytester: pytest.Pytester,
) -> None:
    index_pytester.makepyfile(
        test_sample="""
        import unittest

        class DatesTests(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise AssertionError

            def test_first(self):
                pass

            def test_second(self):
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
        tests = ["test_sample.py::DatesTests"]
        """,
    )

    result = index_pytester.runpytest("-p", "no:django")

    result.assert_outcomes(xfailed=2)


def test_invalid_index_stops_run_before_starting_workers(
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
        name = "dates"
        kind = "pendng"
        targets = ["pyturso"]
        tests = ["test_sample.py::test_fails"]
        """,
    )

    result = index_pytester.runpytest("-p", "no:django", "-n", "2")

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert result.stderr.lines == ["ERROR: cause 'dates': unknown kind 'pendng'", ""]
