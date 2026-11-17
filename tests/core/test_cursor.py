import datetime as dt
import decimal
import typing as t
import uuid

import pytest

from dj_turso import base


def test_format_placeholders_bind_positionally(target: base.DatabaseWrapper) -> None:
    with target.cursor() as cursor:
        cursor.execute("SELECT %s, %s", [1, "a"])
        rows = cursor.fetchall()

    assert rows == [(1, "a")]


def test_pyformat_placeholders_bind_by_name(target: base.DatabaseWrapper) -> None:
    with target.cursor() as cursor:
        cursor.execute("SELECT %(first)s, %(second)s", {"first": 1, "second": 2})
        rows = cursor.fetchall()

    assert rows == [(1, 2)]


def test_percent_literal_untouched_without_params(
    target: base.DatabaseWrapper,
) -> None:
    with target.cursor() as cursor:
        cursor.execute("SELECT 'a%s'")
        rows = cursor.fetchall()

    assert rows == [("a%s",)]


def test_doubled_percent_collapses_with_params(target: base.DatabaseWrapper) -> None:
    with target.cursor() as cursor:
        cursor.execute("SELECT '100%%', %s", [1])
        rows = cursor.fetchall()

    assert rows == [("100%", 1)]


@pytest.mark.parametrize(
    ("stored", "value"),
    [
        pytest.param("2026-11-16", dt.date(2026, 11, 16), id="date"),
        pytest.param(
            "2026-11-16 01:02:03.000004",
            dt.datetime.fromisoformat("2026-11-16T01:02:03.000004"),
            id="datetime",
        ),
        pytest.param("01:02:03", dt.time(1, 2, 3), id="time"),
        pytest.param("1.10", decimal.Decimal("1.10"), id="decimal"),
        pytest.param(b"ab", memoryview(b"ab"), id="memoryview"),
        pytest.param(
            "12345678-1234-5678-1234-567812345678",
            uuid.UUID("12345678-1234-5678-1234-567812345678"),
            id="uuid",
        ),
    ],
)
def test_python_value_binds_as_sqlite_storage_form(
    stored: object, target: base.DatabaseWrapper, value: t.Any
) -> None:
    with target.cursor() as cursor:
        cursor.execute("SELECT %s", [value])
        rows = cursor.fetchall()

    assert rows == [(stored,)]


def test_executemany_binds_each_mapping_by_name(
    target: base.DatabaseWrapper,
) -> None:
    with target.cursor() as cursor:
        cursor.execute("CREATE TABLE item (name TEXT, added TEXT)")
        cursor.executemany(
            "INSERT INTO item VALUES (%(name)s, %(added)s)",
            [
                {"added": dt.date(2026, 11, 16), "name": "a"},
                {"added": dt.date(2026, 11, 17), "name": "b"},
            ],
        )
        rowcount = cursor.rowcount
        cursor.execute("SELECT name, added FROM item")
        rows = cursor.fetchall()

    assert rowcount == 2
    assert rows == [("a", "2026-11-16"), ("b", "2026-11-17")]


def test_executemany_binds_each_sequence_by_position(
    target: base.DatabaseWrapper,
) -> None:
    with target.cursor() as cursor:
        cursor.execute("CREATE TABLE item (name TEXT, price TEXT)")
        cursor.executemany(
            "INSERT INTO item VALUES (%s, %s)",
            [("a", decimal.Decimal("1.10")), ("b", decimal.Decimal("2.20"))],
        )
        rowcount = cursor.rowcount
        cursor.execute("SELECT name, price FROM item")
        rows = cursor.fetchall()

    assert rowcount == 2
    assert rows == [("a", "1.10"), ("b", "2.20")]


def test_executemany_without_params_changes_nothing(
    target: base.DatabaseWrapper,
) -> None:
    with target.cursor() as cursor:
        cursor.execute("CREATE TABLE item (name TEXT)")
        cursor.executemany("INSERT INTO item VALUES (%s)", [])
        rowcount = cursor.rowcount
        cursor.execute("SELECT name FROM item")
        rows = cursor.fetchall()

    assert rowcount == 0
    assert rows == []
