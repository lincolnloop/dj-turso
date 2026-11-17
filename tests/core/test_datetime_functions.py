import datetime as dt

import django.db
import pytest
from django.db.models import functions
from django.utils import timezone
from pytest_django import Settings

from dj_turso import exceptions
from tests.core import models


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("expected", "lookup"),
    [
        pytest.param(2016, "year", id="year"),
        pytest.param(2015, "iso_year", id="iso_year"),
        pytest.param(1, "quarter", id="quarter"),
        pytest.param(1, "month", id="month"),
        pytest.param(53, "week", id="week"),
        pytest.param(6, "week_day", id="week_day"),
        pytest.param(5, "iso_week_day", id="iso_week_day"),
        pytest.param(1, "day", id="day"),
        pytest.param(23, "hour", id="hour"),
        pytest.param(30, "minute", id="minute"),
        pytest.param(50, "second", id="second"),
    ],
)
def test_extract_reads_datetime_part(expected: int, lookup: str) -> None:
    models.Event.objects.create(start=dt.datetime(2016, 1, 1, 23, 30, 50, 321, dt.UTC))

    values = models.Event.objects.values_list(
        functions.Extract("start", lookup), flat=True
    )

    assert list(values) == [expected]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("expected", "lookup"),
    [
        pytest.param(2016, "year", id="year"),
        pytest.param(2015, "iso_year", id="iso_year"),
        pytest.param(1, "quarter", id="quarter"),
        pytest.param(1, "month", id="month"),
        pytest.param(53, "week", id="week"),
        pytest.param(1, "week_day", id="week_day"),
        pytest.param(7, "iso_week_day", id="iso_week_day"),
        pytest.param(3, "day", id="day"),
    ],
)
def test_extract_reads_date_part(expected: int, lookup: str) -> None:
    models.Event.objects.create(day=dt.date(2016, 1, 3))

    values = models.Event.objects.values_list(
        functions.Extract("day", lookup), flat=True
    )

    assert list(values) == [expected]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("expected", "lookup"),
    [
        pytest.param(23, "hour", id="hour"),
        pytest.param(30, "minute", id="minute"),
        pytest.param(50, "second", id="second"),
    ],
)
def test_extract_reads_time_part(expected: int, lookup: str) -> None:
    models.Event.objects.create(time=dt.time(23, 30, 50, 321))

    values = models.Event.objects.values_list(
        functions.Extract("time", lookup), flat=True
    )

    assert list(values) == [expected]


@pytest.mark.django_db
def test_extract_reads_naive_datetime_without_use_tz(
    settings: Settings,
) -> None:
    settings.USE_TZ = False
    models.Event.objects.create(start="2016-01-01 23:30:50")

    values = models.Event.objects.values_list(functions.ExtractHour("start"), flat=True)

    assert list(values) == [23]


@pytest.mark.django_db
def test_extract_from_null_is_null() -> None:
    models.Event.objects.create()

    values = models.Event.objects.values_list(functions.ExtractYear("start"), flat=True)

    assert list(values) == [None]


@pytest.mark.django_db
def test_extract_unknown_part_raises() -> None:
    queryset = models.Event.objects.values_list(
        functions.Extract("start", "fortnight"), flat=True
    )

    with pytest.raises(exceptions.UnknownLookupTypeError) as excinfo:
        list(queryset)

    assert str(excinfo.value) == "Turso cannot extract 'fortnight' from a datetime."


@pytest.mark.django_db
def test_extract_in_other_time_zone_raises_not_supported() -> None:
    queryset = models.Event.objects.values_list(
        functions.ExtractYear("start"), flat=True
    )

    with (
        timezone.override("Europe/Paris"),
        pytest.raises(django.db.NotSupportedError) as excinfo,
    ):
        list(queryset)

    assert str(excinfo.value) == (
        "Turso's date functions cannot convert from the database time zone 'UTC' "
        "to 'Europe/Paris'. Query in 'UTC' instead."
    )
