"""Turso and pyturso behavior dj-turso relies on or works around.

A failure here means upstream changed: either fix what broke, or delete the
workaround it made unnecessary. tests/assumptions.toml says which code each
test backs.
"""

import collections.abc
import pathlib
import typing as t

import pytest
import turso
from turso import _turso


@pytest.mark.parametrize(
    ("sql", "rows"),
    [
        pytest.param(
            "SELECT strftime('%f', '2020-01-01 00:00:00.123456')",
            [("00.123",)],
            id="date-functions-keep-milliseconds",
        ),
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
            "SELECT sqlite_version()",
            [("3.50.4",)],
            id="sqlite-dialect-version",
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
        pytest.param(
            'near "%": syntax error',
            "SELECT %s",
            id="no-format-placeholders",
        ),
        pytest.param(
            "Parse error: ORDER BY clause is not supported yet in aggregate functions",
            "SELECT group_concat(x, ',' ORDER BY x) FROM (SELECT 'a' AS x)",
            id="no-aggregate-order-by",
        ),
        pytest.param(
            "Parse error: too many columns in result set",
            "SELECT " + ", ".join(["1"] * 2001),
            id="no-more-than-2000-columns",
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


def test_turso_applies_foreign_keys_pragma_inside_transaction() -> None:
    connection = turso.connect(":memory:", isolation_level=None)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("BEGIN")
    connection.execute("PRAGMA foreign_keys = ON")

    assert connection.execute("PRAGMA foreign_keys").fetchall() == [(1,)]


@pytest.mark.parametrize(
    ("parameters", "sql"),
    [
        pytest.param({"name": 1}, "SELECT :name", id="named"),
        pytest.param((1,), "SELECT ?", id="qmark"),
    ],
)
def test_pyturso_binds_placeholders(
    parameters: dict[str, int] | tuple[int], sql: str
) -> None:
    connection = turso.connect(":memory:", isolation_level=None)

    assert connection.execute(sql, parameters).fetchall() == [(1,)]


@pytest.mark.parametrize(
    ("make_owner", "message", "name"),
    [
        pytest.param(
            lambda: turso,
            "module 'turso' has no attribute 'Binary'",
            "Binary",
            id="no-binary",
        ),
        pytest.param(
            lambda: turso.connect(":memory:", isolation_level=None),
            "'Connection' object has no attribute 'backup'",
            "backup",
            id="no-backup",
        ),
        pytest.param(
            lambda: turso.connect(":memory:", isolation_level=None),
            "'Connection' object has no attribute 'create_function'",
            "create_function",
            id="no-create-function",
        ),
    ],
)
def test_pyturso_lacks_stdlib_api(
    make_owner: collections.abc.Callable[[], object], message: str, name: str
) -> None:
    owner = make_owner()

    with pytest.raises(AttributeError) as excinfo:
        getattr(owner, name)

    assert str(excinfo.value) == message


@pytest.mark.parametrize(
    ("option", "value"),
    [
        pytest.param("check_same_thread", False, id="check-same-thread"),
        pytest.param("detect_types", 0, id="detect-types"),
        pytest.param("timeout", 1, id="timeout"),
        pytest.param("uri", True, id="uri"),
    ],
)
def test_pyturso_connect_refuses_stdlib_option(option: str, value: t.Any) -> None:
    with pytest.raises(TypeError) as excinfo:
        turso.connect(":memory:", **{option: value})

    assert str(excinfo.value) == (
        f"connect() got an unexpected keyword argument '{option}'"
    )


def test_pyturso_executemany_without_parameters_reports_minus_one() -> None:
    connection = turso.connect(":memory:", isolation_level=None)
    connection.execute("CREATE TABLE p (id INTEGER PRIMARY KEY)")

    cursor = connection.executemany("INSERT INTO p VALUES (?)", [])

    assert cursor.rowcount == -1


def test_pyturso_binds_integer_past_64_bits_as_float() -> None:
    connection = turso.connect(":memory:", isolation_level=None)

    rows = connection.execute("SELECT ?", (2**70 + 1,)).fetchall()

    assert rows == [(1.1805916207174113e21,)]


def test_pyturso_refuses_unbindable_parameter() -> None:
    connection = turso.connect(":memory:", isolation_level=None)

    with pytest.raises(turso.DatabaseError) as excinfo:
        connection.execute("SELECT ?", (object(),))

    assert str(excinfo.value) == (
        "unexpected parameter value, "
        "only None, numbers, strings and bytes are supported"
    )


def test_pyturso_returning_write_raises_constraint_error_on_fetch() -> None:
    connection = turso.connect(":memory:", isolation_level=None)
    connection.execute("CREATE TABLE u (a INT UNIQUE)")
    connection.execute("INSERT INTO u VALUES (1)")
    cursor = connection.cursor()
    cursor.execute("INSERT INTO u VALUES (1) RETURNING a")

    with pytest.raises(turso.IntegrityError) as excinfo:
        cursor.fetchall()

    assert str(excinfo.value) == "UNIQUE constraint failed: u.a"


def test_pyturso_raises_engine_error_as_bare_database_error() -> None:
    connection = turso.connect(":memory:", isolation_level=None)

    with pytest.raises(turso.DatabaseError) as excinfo:
        connection.execute("SELECT * FROM missing")

    assert type(excinfo.value) is turso.DatabaseError
    assert type(excinfo.value.__context__) is _turso.Error
