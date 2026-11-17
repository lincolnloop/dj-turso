import errno
import os

import pytest


def test_unreachable_sqld_fails_remote_test_with_compose_hint(
    monkeypatch: pytest.MonkeyPatch, pytester: pytest.Pytester
) -> None:
    pytester.makeconftest("from tests.core.conftest import sqld_url")
    pytester.makepyfile(
        test_sample="""
        def test_remote(sqld_url):
            pass
        """
    )
    monkeypatch.setenv("DJ_TURSO_SQLD_PORT", "1")

    recorder = pytester.inline_run()

    report = recorder.matchreport("test_remote", when="setup")
    assert report.outcome == "failed"
    assert str(report.longrepr) == (
        "sqld is not reachable at http://127.0.0.1:1 "
        f"([Errno {errno.ECONNREFUSED}] {os.strerror(errno.ECONNREFUSED)}): "
        "run `docker compose up -d --wait`"
    )
