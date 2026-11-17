import decimal
import math
import uuid

import django.db
import pytest
from django.db.models import expressions, fields, functions

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
def test_bitand_combines_operands() -> None:
    models.Tag.objects.create(name="one")

    values = models.Tag.objects.values_list(expressions.Value(6).bitand(3), flat=True)

    assert list(values) == [2]


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
