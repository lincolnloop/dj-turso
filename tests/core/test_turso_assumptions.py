"""Turso and pyturso behavior dj-turso relies on or works around.

A failure here means upstream changed: either fix what broke, or delete the
workaround it made unnecessary. tests/assumptions.toml says which code each
test backs.
"""

import pathlib
import typing as t

import pytest
import turso


@pytest.mark.parametrize(
    ("sql", "rows"),
    [
        pytest.param(
            "SELECT datetime('2020-01-01', 'Europe/Paris')",
            [(None,)],
            id="date-functions-know-no-time-zones",
        ),
        pytest.param(
            "SELECT ROUND(1234.5, -2)",
            [(1235.0,)],
            id="negative-round-precision-ignored",
        ),
        pytest.param(
            "SELECT 'aa' REGEXP '(a)\\1'",
            [(None,)],
            id="regexp-backreference-is-null",
        ),
        pytest.param(
            "SELECT STDDEV(x) FROM (SELECT 1 AS x UNION ALL SELECT 3)",
            [(1.4142135623730951,)],
            id="stddev-is-sample",
        ),
        pytest.param(
            "SELECT typeof(uuid4()), typeof(uuid7())",
            [("blob", "blob")],
            id="uuid-functions-return-blob",
        ),
    ],
)
def test_turso_answers(rows: list[tuple[t.Any, ...]], sql: str) -> None:
    connection = turso.connect(":memory:", isolation_level=None)

    assert connection.execute(sql).fetchall() == rows


@pytest.mark.parametrize(
    ("message", "sql"),
    [
        pytest.param(
            "Parse error: no such function: BIT_AND",
            "SELECT BIT_AND(1)",
            id="no-bit-and-aggregate",
        ),
        pytest.param(
            "Parse error: no such function: BIT_OR",
            "SELECT BIT_OR(1)",
            id="no-bit-or-aggregate",
        ),
        pytest.param(
            "Parse error: no such function: BIT_XOR",
            "SELECT BIT_XOR(1)",
            id="no-bit-xor-aggregate",
        ),
        pytest.param(
            "Parse error: Generated columns require "
            "--experimental-generated-columns flag",
            "CREATE TABLE g (a INT, b INT GENERATED ALWAYS AS (a + 1) VIRTUAL)",
            id="generated-columns-need-experimental-flag",
        ),
        pytest.param(
            "Parse error: no such function: md5",
            "SELECT md5('a')",
            id="no-md5-function",
        ),
        pytest.param(
            "Parse error: no such function: sha1",
            "SELECT sha1('a')",
            id="no-sha1-function",
        ),
        pytest.param(
            "Parse error: no such function: sha224",
            "SELECT sha224('a')",
            id="no-sha224-function",
        ),
        pytest.param(
            "Parse error: no such function: sha256",
            "SELECT sha256('a')",
            id="no-sha256-function",
        ),
        pytest.param(
            "Parse error: no such function: sha384",
            "SELECT sha384('a')",
            id="no-sha384-function",
        ),
        pytest.param(
            "Parse error: no such function: sha512",
            "SELECT sha512('a')",
            id="no-sha512-function",
        ),
        pytest.param(
            'near "0": syntax error',
            "SELECT 1 XOR 0",
            id="no-xor-operator",
        ),
    ],
)
def test_turso_refuses(message: str, sql: str) -> None:
    connection = turso.connect(":memory:", isolation_level=None)

    with pytest.raises(turso.DatabaseError) as excinfo:
        connection.execute(sql)

    assert str(excinfo.value) == message


def test_turso_vacuum_into_refuses_parameter(tmp_path: pathlib.Path) -> None:
    connection = turso.connect(":memory:", isolation_level=None)

    with pytest.raises(turso.DatabaseError) as excinfo:
        connection.execute("VACUUM INTO ?", (str(tmp_path / "copy.db"),))

    assert (
        str(excinfo.value) == "Parse error: VACUUM INTO requires a string literal path"
    )


def test_turso_foreign_key_check_reports_no_rows() -> None:
    connection = turso.connect(":memory:", isolation_level=None)
    connection.execute("CREATE TABLE p (id INTEGER PRIMARY KEY)")
    connection.execute(
        "CREATE TABLE ch (id INTEGER PRIMARY KEY, p_id INTEGER REFERENCES p(id))"
    )
    connection.execute("INSERT INTO ch VALUES (1, 99)")

    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_turso_keeps_autoincrement_counter_in_own_table() -> None:
    connection = turso.connect(":memory:", isolation_level=None)
    connection.execute("CREATE TABLE ai (id INTEGER PRIMARY KEY AUTOINCREMENT)")

    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()

    assert rows == [
        ("__turso_internal_seq___turso_internal_autoincrement_ai",),
        ("ai",),
        ("sqlite_sequence",),
    ]


def test_turso_table_xinfo_drops_type_arguments() -> None:
    connection = turso.connect(":memory:", isolation_level=None)
    connection.execute("CREATE TABLE v (n varchar(30))")

    assert connection.execute("PRAGMA table_xinfo(v)").fetchall() == [
        (0, "n", "varchar", 0, None, 0, 0)
    ]


def test_turso_lowercases_index_names() -> None:
    connection = turso.connect(":memory:", isolation_level=None)
    connection.execute("CREATE TABLE v (n TEXT)")
    connection.execute('CREATE INDEX "MyIdx" ON v (n)')

    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'"
    ).fetchall()

    assert rows == [("myidx",)]


def test_turso_memory_database_is_per_connection() -> None:
    first = turso.connect(":memory:", isolation_level=None)
    first.execute("CREATE TABLE z (a)")
    second = turso.connect(":memory:", isolation_level=None)

    assert second.execute("SELECT name FROM sqlite_master").fetchall() == []
