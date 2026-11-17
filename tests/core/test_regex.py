import django.db
import pytest
from django.test import utils as test_utils

from tests.core import models


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("lookup", "pattern"),
    [
        pytest.param("name__regex", r"(?>ab)c", id="atomic-group"),
        pytest.param("name__regex", r"b(.).*b\1", id="backreference"),
        pytest.param("name__regex", r"(a)?(?(1)b|c)", id="conditional"),
        pytest.param("name__iregex", r"b(.).*b\1", id="iregex-backreference"),
        pytest.param("name__regex", r"foo(?=bar)", id="lookahead"),
        pytest.param("name__regex", r"(?<=foo)bar", id="lookbehind"),
    ],
)
def test_regex_turso_cannot_compile_raises_not_supported_error(
    lookup: str, pattern: str
) -> None:
    models.Tag.objects.create(name="foobar")

    with pytest.raises(django.db.NotSupportedError) as excinfo:
        list(models.Tag.objects.filter(**{lookup: pattern}))

    assert str(excinfo.value) == (
        f"Turso cannot compile the regular expression {pattern!r}: its REGEXP "
        "follows the syntax of Rust's regex crate, not Python's re module."
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("lookup", "pattern", "names"),
    [
        pytest.param("name__regex", r"ba[rz]$", ["foobar", "foobaz"], id="class"),
        pytest.param("name__regex", r"oo(f|b)", ["foobar", "foobaz"], id="alternation"),
        pytest.param("name__regex", r"^b", ["barfoo"], id="anchor"),
        pytest.param("name__regex", r"(?i)^B", ["barfoo"], id="inline-flag"),
        pytest.param("name__iregex", r"FOO$", ["barfoo"], id="iregex"),
    ],
)
def test_regex_turso_compiles_matches(
    lookup: str, names: list[str], pattern: str
) -> None:
    models.Tag.objects.bulk_create(
        models.Tag(name=name) for name in ["barfoo", "foobar", "foobaz"]
    )

    matches = models.Tag.objects.filter(**{lookup: pattern}).order_by("name")

    assert list(matches.values_list("name", flat=True)) == names


@pytest.mark.django_db
def test_regex_pattern_is_checked_once() -> None:
    queryset = models.Tag.objects.filter(name__regex="checked once").values_list(
        "name", flat=True
    )

    with test_utils.CaptureQueriesContext(django.db.connection) as first:
        list(queryset.all())
    with test_utils.CaptureQueriesContext(django.db.connection) as second:
        list(queryset.all())

    lookup_sql = (
        'SELECT "core_tag"."name" AS "name" FROM "core_tag" '
        'WHERE "core_tag"."name" REGEXP \'checked once\''
    )
    assert [query["sql"] for query in first.captured_queries] == [
        "SELECT regexp('checked once', '')",
        lookup_sql,
    ]
    assert [query["sql"] for query in second.captured_queries] == [lookup_sql]
