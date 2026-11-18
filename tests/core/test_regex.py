import uuid

import django.db
import pytest
from django.db.models import expressions
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
def test_regex_value_turso_cannot_compile_raises_not_supported_error() -> None:
    models.Tag.objects.create(name="foobar")
    queryset = models.Tag.objects.filter(name__regex=expressions.Value(r"b(.).*b\1"))

    with pytest.raises(django.db.NotSupportedError) as excinfo:
        list(queryset)

    assert str(excinfo.value) == (
        "Turso cannot compile the regular expression 'b(.).*b\\\\1': its REGEXP "
        "follows the syntax of Rust's regex crate, not Python's re module."
    )


@pytest.mark.django_db
def test_regex_value_turso_compiles_matches() -> None:
    models.Tag.objects.bulk_create(models.Tag(name=name) for name in ["bar", "foo"])

    names = models.Tag.objects.filter(name__regex=expressions.Value("^b")).values_list(
        "name", flat=True
    )

    assert list(names) == ["bar"]


@pytest.mark.django_db
def test_regex_column_pattern_turso_cannot_compile_matches_nothing() -> None:
    models.Tag.objects.bulk_create(models.Tag(name=name) for name in ["bar", "(?=b)"])

    names = models.Tag.objects.filter(name__regex=expressions.F("name")).values_list(
        "name", flat=True
    )

    assert list(names) == ["bar"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("lookup", "names", "pattern"),
    [
        pytest.param("name__regex", ["foobar", "foobaz"], r"ba[rz]$", id="class"),
        pytest.param("name__regex", ["foobar", "foobaz"], r"oo(f|b)", id="alternation"),
        pytest.param("name__regex", ["barfoo"], r"^b", id="anchor"),
        pytest.param("name__regex", ["barfoo"], r"(?i)^B", id="inline-flag"),
        pytest.param("name__iregex", ["barfoo"], r"FOO$", id="iregex"),
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
@pytest.mark.parametrize(
    ("names", "pattern"),
    [
        pytest.param(["foo"], r"foo$", id="end-of-text"),
        pytest.param(["foo", "foo\n"], r"(?m)foo$", id="end-of-line"),
    ],
)
def test_regex_dollar_anchors_to_end_of_text(names: list[str], pattern: str) -> None:
    models.Tag.objects.bulk_create(models.Tag(name=name) for name in ["foo", "foo\n"])

    matches = models.Tag.objects.filter(name__regex=pattern).order_by("name")

    assert list(matches.values_list("name", flat=True)) == names


@pytest.mark.django_db
def test_regex_possessive_quantifier_repeats_repetition() -> None:
    models.Tag.objects.create(name="aa")

    names = models.Tag.objects.filter(name__regex=r"a*+a").values_list(
        "name", flat=True
    )

    assert list(names) == ["aa"]


# The check's cache lasts the process: a pattern of its own keeps the test independent.
@pytest.mark.django_db
def test_regex_pattern_is_checked_once() -> None:
    pattern = f"checked once {uuid.uuid4().hex}"
    queryset = models.Tag.objects.filter(name__regex=pattern).values_list(
        "name", flat=True
    )

    with test_utils.CaptureQueriesContext(django.db.connection) as first:
        list(queryset.all())
    with test_utils.CaptureQueriesContext(django.db.connection) as second:
        list(queryset.all())

    lookup_sql = (
        'SELECT "core_tag"."name" AS "name" FROM "core_tag" '
        'WHERE "core_tag"."name" REGEXP \'<pattern>\''
    )
    assert [
        query["sql"].replace(pattern, "<pattern>") for query in first.captured_queries
    ] == ["SELECT regexp('<pattern>', '')", lookup_sql]
    assert [
        query["sql"].replace(pattern, "<pattern>") for query in second.captured_queries
    ] == [lookup_sql]


@pytest.mark.django_db
def test_regex_in_subquery_matches() -> None:
    models.Tag.objects.bulk_create(models.Tag(name=name) for name in ["bar", "foo"])

    names = models.Tag.objects.filter(
        pk__in=models.Tag.objects.filter(name__regex="^b").values("pk")
    ).values_list("name", flat=True)

    assert list(names) == ["bar"]


@pytest.mark.django_db
def test_regex_check_constraint_rejects_non_matching_row() -> None:
    with pytest.raises(django.db.IntegrityError) as excinfo:
        models.Slug.objects.create(name="Not A Slug")

    assert str(excinfo.value) == "CHECK constraint failed: slug_lowercase"


@pytest.mark.django_db
def test_regex_unique_constraint_condition_rejects_matching_duplicate() -> None:
    models.Slug.objects.bulk_create(
        models.Slug(name=name) for name in ["draft-a", "post", "post"]
    )

    with pytest.raises(django.db.IntegrityError) as excinfo:
        models.Slug.objects.create(name="draft-a")

    assert str(excinfo.value) == "UNIQUE constraint failed: core_slug.name"
