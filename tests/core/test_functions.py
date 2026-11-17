import collections.abc
import decimal
import functools
import math
import operator
import statistics
import uuid

import django.db
import pytest
from django.db.models import Q, aggregates, expressions, fields, functions

from tests.core import models


@pytest.mark.django_db
def test_cot_is_reciprocal_of_tangent() -> None:
    models.Tag.objects.create(name="one")

    values = models.Tag.objects.values_list(
        functions.Cot(expressions.Value(1.0)), flat=True
    )

    assert list(values) == [1 / math.tan(1.0)]


@pytest.mark.django_db
def test_cot_of_decimal_compares_as_number() -> None:
    models.Tag.objects.create(name="one")

    names = (
        models.Tag.objects.annotate(
            cot=functions.Cot(expressions.Value(decimal.Decimal("1.0")))
        )
        .filter(cot__gt=0)
        .values_list("name", flat=True)
    )

    assert list(names) == ["one"]


@pytest.mark.django_db
def test_random_is_in_unit_interval() -> None:
    models.Tag.objects.bulk_create(models.Tag(name=str(number)) for number in range(50))

    values = models.Tag.objects.values_list(functions.Random(), flat=True)

    assert all(0 <= value < 1 for value in values) is True


@pytest.mark.django_db
def test_order_by_random_returns_every_row() -> None:
    models.Tag.objects.bulk_create(models.Tag(name=name) for name in "abc")

    names = models.Tag.objects.order_by("?").values_list("name", flat=True)

    assert sorted(names) == ["a", "b", "c"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("expected", "lhs", "rhs"),
    [
        pytest.param(6, 5, 3, id="integers"),
        pytest.param(None, None, 3, id="null-lhs"),
        pytest.param(None, 5, None, id="null-rhs"),
    ],
)
def test_bitxor_combines_operands(
    expected: int | None, lhs: int | None, rhs: int | None
) -> None:
    models.Tag.objects.create(name="one")

    values = models.Tag.objects.values_list(
        expressions.CombinedExpression(
            expressions.Value(lhs, fields.IntegerField()),
            expressions.Combinable.BITXOR,
            expressions.Value(rhs, fields.IntegerField()),
        ),
        flat=True,
    )

    assert list(values) == [expected]


@pytest.mark.django_db
def test_bitxor_of_window_operand_reads_each_row() -> None:
    models.Tag.objects.bulk_create(models.Tag(name=name) for name in "abc")

    values = models.Tag.objects.order_by("name").values_list(
        expressions.Window(functions.RowNumber(), order_by="name").bitxor(3),
        flat=True,
    )

    assert list(values) == [2, 1, 0]


@pytest.mark.django_db
def test_bitxor_of_aggregate_operand() -> None:
    models.Tag.objects.bulk_create(models.Tag(name=name) for name in "abc")

    result = models.Tag.objects.aggregate(xor=aggregates.Count("pk").bitxor(1))

    assert result == {"xor": 2}


@pytest.mark.django_db
def test_bitand_combines_operands() -> None:
    models.Tag.objects.create(name="one")

    values = models.Tag.objects.values_list(expressions.Value(6).bitand(3), flat=True)

    assert list(values) == [2]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("expected", "precision", "value"),
    [
        pytest.param(decimal.Decimal(370), -1, decimal.Decimal("365.25"), id="decimal"),
        pytest.param(400.0, -2, 365.25, id="float"),
        pytest.param(370, -1, 365, id="integer"),
        pytest.param(-370, -1, -365, id="negative-half"),
        pytest.param(0, -3, 365, id="beyond-value"),
    ],
)
def test_round_to_negative_precision_rounds_left_of_point(
    expected: decimal.Decimal | float,
    precision: int,
    value: decimal.Decimal | float,
) -> None:
    models.Tag.objects.create(name="one")

    values = models.Tag.objects.values_list(
        functions.Round(expressions.Value(value), precision), flat=True
    )

    assert list(values) == [expected]
    assert [type(result) for result in values] == [type(expected)]


@pytest.mark.django_db
def test_round_null_to_negative_precision_is_null() -> None:
    models.Tag.objects.create(name="one")

    values = models.Tag.objects.values_list(
        functions.Round(expressions.Value(None, fields.IntegerField()), -1), flat=True
    )

    assert list(values) == [None]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("function", "version"),
    [
        pytest.param(functions.UUID4, 4, id="uuid4"),
        pytest.param(functions.UUID7, 7, id="uuid7"),
    ],
)
def test_uuid_function_generates_version(
    function: type[expressions.Func], version: int
) -> None:
    models.Tag.objects.create(name="one")

    values = models.Tag.objects.values_list(function(), flat=True)

    assert [value.version for value in values] == [version]
    assert [type(value) for value in values] == [uuid.UUID]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("function", "name"),
    [
        pytest.param(functions.MD5, "MD5", id="md5"),
        pytest.param(functions.SHA1, "SHA1", id="sha1"),
        pytest.param(functions.SHA224, "SHA224", id="sha224"),
        pytest.param(functions.SHA256, "SHA256", id="sha256"),
        pytest.param(functions.SHA384, "SHA384", id="sha384"),
        pytest.param(functions.SHA512, "SHA512", id="sha512"),
    ],
)
def test_hash_function_is_not_supported(
    function: type[expressions.Func], name: str
) -> None:
    queryset = models.Tag.objects.values_list(function("name"), flat=True)

    with pytest.raises(django.db.NotSupportedError) as excinfo:
        list(queryset)

    assert str(excinfo.value) == f"Turso has no {name} function to hash with."


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("aggregate", "expected"),
    [
        pytest.param(
            aggregates.StdDev(functions.Length("name"), filter=Q(name__startswith="a")),
            statistics.pstdev([2, 4, 4, 4, 5, 5, 7, 9]),
            id="stddev-pop",
        ),
        pytest.param(
            aggregates.StdDev(
                functions.Length("name"), filter=Q(name__startswith="a"), sample=True
            ),
            statistics.stdev([2, 4, 4, 4, 5, 5, 7, 9]),
            id="stddev-sample",
        ),
        pytest.param(
            aggregates.Variance(
                functions.Length("name"), filter=Q(name__startswith="a")
            ),
            statistics.pvariance([2, 4, 4, 4, 5, 5, 7, 9]),
            id="variance-pop",
        ),
        pytest.param(
            aggregates.Variance(
                functions.Length("name"), filter=Q(name__startswith="a"), sample=True
            ),
            statistics.variance([2, 4, 4, 4, 5, 5, 7, 9]),
            id="variance-sample",
        ),
    ],
)
def test_spread_aggregate_matches_statistics(
    aggregate: aggregates.Aggregate, expected: float
) -> None:
    models.Tag.objects.bulk_create(
        [
            *(models.Tag(name="a" * length) for length in [2, 4, 4, 4, 5, 5, 7, 9]),
            models.Tag(name="b" * 30),
        ]
    )

    result = models.Tag.objects.aggregate(spread=aggregate)

    assert result == {"spread": pytest.approx(expected)}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("aggregate", "expected"),
    [
        pytest.param(aggregates.StdDev("name"), 0.0, id="stddev-pop"),
        pytest.param(aggregates.StdDev("name", sample=True), None, id="stddev-sample"),
        pytest.param(aggregates.Variance("name"), 0.0, id="variance-pop"),
        pytest.param(
            aggregates.Variance("name", sample=True), None, id="variance-sample"
        ),
    ],
)
def test_spread_aggregate_of_one_value(
    aggregate: aggregates.Aggregate, expected: float | None
) -> None:
    models.Tag.objects.create(name="3")

    result = models.Tag.objects.aggregate(spread=aggregate)

    assert result == {"spread": expected}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "aggregate",
    [
        pytest.param(aggregates.StdDev("name"), id="stddev-pop"),
        pytest.param(aggregates.StdDev("name", sample=True), id="stddev-sample"),
        pytest.param(aggregates.Variance("name"), id="variance-pop"),
        pytest.param(aggregates.Variance("name", sample=True), id="variance-sample"),
    ],
)
def test_spread_aggregate_of_no_rows_is_null(aggregate: aggregates.Aggregate) -> None:
    result = models.Tag.objects.aggregate(spread=aggregate)

    assert result == {"spread": None}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("aggregate", "bitwise_operator"),
    [
        pytest.param(aggregates.BitAnd, operator.and_, id="bit-and"),
        pytest.param(aggregates.BitOr, operator.or_, id="bit-or"),
        pytest.param(aggregates.BitXor, operator.xor, id="bit-xor"),
    ],
)
def test_bit_aggregate_matches_operator(
    aggregate: type[aggregates.Aggregate],
    bitwise_operator: collections.abc.Callable[[int, int], int],
) -> None:
    models.Tag.objects.bulk_create(
        models.Tag(name=prefix * length)
        for prefix, length in [("a", 3), ("a", 6), ("b", 1), ("a", 13)]
    )

    result = models.Tag.objects.aggregate(
        bits=aggregate(functions.Length("name") - 10, filter=Q(name__startswith="a"))
    )

    assert result == {
        "bits": functools.reduce(bitwise_operator, [3 - 10, 6 - 10, 13 - 10])
    }


@pytest.mark.django_db
@pytest.mark.parametrize(
    "aggregate",
    [
        pytest.param(aggregates.BitAnd, id="bit-and"),
        pytest.param(aggregates.BitOr, id="bit-or"),
        pytest.param(aggregates.BitXor, id="bit-xor"),
    ],
)
def test_bit_aggregate_of_no_rows_is_null(
    aggregate: type[aggregates.Aggregate],
) -> None:
    result = models.Tag.objects.aggregate(bits=aggregate(functions.Length("name")))

    assert result == {"bits": None}


@pytest.mark.django_db
def test_any_value_returns_a_group_value() -> None:
    models.Tag.objects.bulk_create(models.Tag(name=name) for name in ["a", "a", "b"])

    rows = (
        models.Tag.objects.values("name")
        .annotate(any_name=aggregates.AnyValue("name"))
        .order_by("name")
        .values_list("name", "any_name")
    )

    assert list(rows) == [("a", "a"), ("b", "b")]


@pytest.mark.django_db
def test_bitxor_check_constraint_rejects_row() -> None:
    models.Counter.objects.create(value=5, mask=3)

    with pytest.raises(django.db.IntegrityError) as excinfo:
        models.Counter.objects.create(value=12, mask=3)

    assert str(excinfo.value) == "CHECK constraint failed: counter_xor_below_8"


@pytest.mark.django_db
def test_bitxor_unique_index_rejects_duplicate() -> None:
    models.Counter.objects.create(value=5, mask=3)

    with pytest.raises(django.db.IntegrityError) as excinfo:
        models.Counter.objects.create(value=4, mask=2)

    assert str(excinfo.value) == (
        "UNIQUE constraint failed: "
        'core_counter.((("value" | "mask") - ("value" & "mask")))'
    )
