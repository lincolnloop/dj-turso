import datetime as dt

import django.db
import pytest
import pytest_django
from django.db.models import aggregates, expressions, fields, functions
from django.utils import timezone

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
    settings: pytest_django.Settings,
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


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("expected", "kind"),
    [
        pytest.param(dt.datetime(2015, 1, 1, tzinfo=dt.UTC), "year", id="year"),
        pytest.param(dt.datetime(2015, 7, 1, tzinfo=dt.UTC), "quarter", id="quarter"),
        pytest.param(dt.datetime(2015, 8, 1, tzinfo=dt.UTC), "month", id="month"),
        pytest.param(dt.datetime(2015, 8, 10, tzinfo=dt.UTC), "week", id="week"),
        pytest.param(dt.datetime(2015, 8, 16, tzinfo=dt.UTC), "day", id="day"),
        pytest.param(dt.datetime(2015, 8, 16, 23, tzinfo=dt.UTC), "hour", id="hour"),
        pytest.param(
            dt.datetime(2015, 8, 16, 23, 30, tzinfo=dt.UTC), "minute", id="minute"
        ),
        pytest.param(
            dt.datetime(2015, 8, 16, 23, 30, 50, tzinfo=dt.UTC), "second", id="second"
        ),
    ],
)
def test_trunc_rounds_datetime_down(expected: dt.datetime, kind: str) -> None:
    models.Event.objects.create(start=dt.datetime(2015, 8, 16, 23, 30, 50, 321, dt.UTC))

    values = models.Event.objects.values_list(functions.Trunc("start", kind), flat=True)

    assert list(values) == [expected]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("expected", "kind"),
    [
        pytest.param(dt.date(2015, 1, 1), "year", id="year"),
        pytest.param(dt.date(2015, 7, 1), "quarter", id="quarter"),
        pytest.param(dt.date(2015, 8, 1), "month", id="month"),
        pytest.param(dt.date(2015, 8, 10), "week", id="week"),
        pytest.param(dt.date(2015, 8, 16), "day", id="day"),
    ],
)
def test_trunc_rounds_date_down(expected: dt.date, kind: str) -> None:
    models.Event.objects.create(day=dt.date(2015, 8, 16))

    values = models.Event.objects.values_list(functions.Trunc("day", kind), flat=True)

    assert list(values) == [expected]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("expected", "kind"),
    [
        pytest.param(dt.time(23), "hour", id="hour"),
        pytest.param(dt.time(23, 30), "minute", id="minute"),
        pytest.param(dt.time(23, 30, 50), "second", id="second"),
    ],
)
def test_trunc_rounds_time_down(expected: dt.time, kind: str) -> None:
    models.Event.objects.create(time=dt.time(23, 30, 50, 321))

    values = models.Event.objects.values_list(functions.Trunc("time", kind), flat=True)

    assert list(values) == [expected]


@pytest.mark.django_db
def test_trunc_null_is_null() -> None:
    models.Event.objects.create()

    values = models.Event.objects.values_list(functions.TruncDay("day"), flat=True)

    assert list(values) == [None]


@pytest.mark.django_db
def test_trunc_unknown_kind_raises() -> None:
    queryset = models.Event.objects.values_list(
        functions.Trunc("start", "fortnight"), flat=True
    )

    with pytest.raises(exceptions.UnknownTruncKindError) as excinfo:
        list(queryset)

    assert str(excinfo.value) == "Turso cannot truncate a datetime to 'fortnight'."


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("expected", "start"),
    [
        pytest.param(
            dt.time(23, 30, 50, 321),
            dt.datetime(2015, 8, 16, 23, 30, 50, 321, dt.UTC),
            id="microseconds",
        ),
        pytest.param(
            dt.time(23, 30, 50),
            dt.datetime(2015, 8, 16, 23, 30, 50, tzinfo=dt.UTC),
            id="whole-seconds",
        ),
    ],
)
def test_trunc_time_keeps_time_of_datetime(
    expected: dt.time, start: dt.datetime
) -> None:
    models.Event.objects.create(start=start)

    values = models.Event.objects.values_list(functions.TruncTime("start"), flat=True)

    assert list(values) == [expected]


@pytest.mark.django_db
def test_trunc_date_keeps_date_of_datetime() -> None:
    models.Event.objects.create(start=dt.datetime(2015, 8, 16, 23, 30, 50, 321, dt.UTC))

    values = models.Event.objects.values_list(functions.TruncDate("start"), flat=True)

    assert list(values) == [dt.date(2015, 8, 16)]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "function",
    [
        pytest.param(functions.TruncDate, id="date"),
        pytest.param(functions.TruncTime, id="time"),
        pytest.param(functions.TruncDay, id="trunc"),
    ],
)
def test_datetime_function_in_other_time_zone_raises_not_supported(
    function: type[expressions.Func],
) -> None:
    queryset = models.Event.objects.values_list(function("start"), flat=True)

    with (
        timezone.override("Europe/Paris"),
        pytest.raises(django.db.NotSupportedError) as excinfo,
    ):
        list(queryset)

    assert str(excinfo.value) == (
        "Turso's date functions cannot convert from the database time zone 'UTC' "
        "to 'Europe/Paris'. Query in 'UTC' instead."
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("duration", "expected", "start"),
    [
        pytest.param(
            dt.timedelta(days=1, microseconds=700_000),
            dt.datetime(2015, 8, 17, 23, 30, 51, 321, dt.UTC),
            dt.datetime(2015, 8, 16, 23, 30, 50, 300_321, dt.UTC),
            id="microseconds",
        ),
        pytest.param(
            dt.timedelta(microseconds=-200),
            dt.datetime(1959, 12, 31, 23, 59, 59, 999_900, dt.UTC),
            dt.datetime(1960, 1, 1, 0, 0, 0, 100, dt.UTC),
            id="before-epoch",
        ),
        pytest.param(
            dt.timedelta(seconds=-50),
            dt.datetime(2015, 8, 16, 23, 30, tzinfo=dt.UTC),
            dt.datetime(2015, 8, 16, 23, 30, 50, tzinfo=dt.UTC),
            id="whole-seconds",
        ),
    ],
)
def test_datetime_plus_duration(
    duration: dt.timedelta, expected: dt.datetime, start: dt.datetime
) -> None:
    models.Event.objects.create(start=start, duration=duration)

    values = models.Event.objects.values_list(
        expressions.F("start") + expressions.F("duration"), flat=True
    )

    assert list(values) == [expected]


@pytest.mark.django_db
def test_duration_plus_datetime() -> None:
    models.Event.objects.create(
        start=dt.datetime(2015, 8, 16, 23, 30, 50, tzinfo=dt.UTC),
        duration=dt.timedelta(hours=1),
    )

    values = models.Event.objects.values_list(
        expressions.F("duration") + expressions.F("start"), flat=True
    )

    assert list(values) == [dt.datetime(2015, 8, 17, 0, 30, 50, tzinfo=dt.UTC)]


@pytest.mark.django_db
def test_datetime_minus_duration() -> None:
    models.Event.objects.create(
        start=dt.datetime(2015, 8, 16, 23, 30, 50, tzinfo=dt.UTC),
        duration=dt.timedelta(days=1, microseconds=1),
    )

    values = models.Event.objects.values_list(
        expressions.F("start") - expressions.F("duration"), flat=True
    )

    assert list(values) == [dt.datetime(2015, 8, 15, 23, 30, 49, 999_999, dt.UTC)]


@pytest.mark.django_db
def test_date_minus_duration_moves_by_whole_days() -> None:
    models.Event.objects.create(
        day=dt.date(2015, 8, 16), duration=dt.timedelta(microseconds=1)
    )

    values = models.Event.objects.values_list(
        expressions.F("day") - expressions.F("duration"), flat=True
    )

    assert list(values) == [dt.datetime(2015, 8, 15, tzinfo=dt.UTC)]


@pytest.mark.django_db
def test_time_plus_duration_wraps_at_midnight() -> None:
    models.Event.objects.create(
        time=dt.time(23, 30, 50, 321), duration=dt.timedelta(hours=1)
    )

    values = models.Event.objects.values_list(
        expressions.F("time") + expressions.F("duration"), flat=True
    )

    assert list(values) == [dt.time(0, 30, 50, 321)]


@pytest.mark.django_db
def test_datetime_plus_null_duration_is_null() -> None:
    models.Event.objects.create(
        start=dt.datetime(2015, 8, 16, 23, 30, 50, tzinfo=dt.UTC)
    )

    values = models.Event.objects.values_list(
        expressions.F("start") + expressions.F("duration"), flat=True
    )

    assert list(values) == [None]


@pytest.mark.django_db
def test_untyped_null_plus_duration_is_null() -> None:
    models.Event.objects.create(duration=dt.timedelta(hours=1))

    values = models.Event.objects.values_list(
        expressions.ExpressionWrapper(
            expressions.Value(None) + expressions.F("duration"),
            output_field=fields.DateTimeField(),
        ),
        flat=True,
    )

    assert list(values) == [None]


@pytest.mark.django_db
def test_duration_times_number() -> None:
    models.Event.objects.create(duration=dt.timedelta(seconds=3))

    values = models.Event.objects.values_list(
        expressions.ExpressionWrapper(
            expressions.F("duration") * 2, output_field=fields.DurationField()
        ),
        flat=True,
    )

    assert list(values) == [dt.timedelta(seconds=6)]


@pytest.mark.django_db
def test_duration_divided_by_number_keeps_fraction() -> None:
    models.Event.objects.create(duration=dt.timedelta(microseconds=3))

    values = models.Event.objects.values_list(
        expressions.ExpressionWrapper(
            expressions.F("duration") / 2, output_field=fields.DurationField()
        ),
        flat=True,
    )

    assert list(values) == [dt.timedelta(microseconds=2)]


@pytest.mark.django_db
def test_datetime_minus_datetime_gives_duration() -> None:
    models.Event.objects.create(
        start=dt.datetime(1960, 1, 1, 0, 0, 0, 1, dt.UTC),
        end=dt.datetime(1960, 1, 2, tzinfo=dt.UTC),
    )

    values = models.Event.objects.values_list(
        expressions.F("start") - expressions.F("end"), flat=True
    )

    assert list(values) == [dt.timedelta(days=-1, microseconds=1)]


@pytest.mark.django_db
def test_date_minus_date_gives_duration() -> None:
    models.Event.objects.create(day=dt.date(2015, 8, 16))

    values = models.Event.objects.values_list(
        expressions.F("day") - expressions.Value(dt.date(2015, 8, 19)), flat=True
    )

    assert list(values) == [dt.timedelta(days=-3)]


@pytest.mark.django_db
def test_time_minus_time_gives_duration() -> None:
    models.Event.objects.create(time=dt.time(12, 30, 15, 2345))

    values = models.Event.objects.values_list(
        expressions.F("time") - expressions.Value(dt.time(11, 15)), flat=True
    )

    assert list(values) == [
        dt.timedelta(hours=1, minutes=15, seconds=15, microseconds=2345)
    ]


@pytest.mark.django_db
def test_datetime_plus_window_duration_reads_each_row() -> None:
    models.Event.objects.bulk_create(
        [
            models.Event(
                start=dt.datetime(2015, 8, 16, tzinfo=dt.UTC),
                duration=dt.timedelta(hours=1),
            ),
            models.Event(
                start=dt.datetime(2015, 8, 17, tzinfo=dt.UTC),
                duration=dt.timedelta(hours=2),
            ),
        ]
    )

    values = models.Event.objects.order_by("start").values_list(
        expressions.F("start") + expressions.Window(aggregates.Max("duration")),
        flat=True,
    )

    assert list(values) == [
        dt.datetime(2015, 8, 16, 2, tzinfo=dt.UTC),
        dt.datetime(2015, 8, 17, 2, tzinfo=dt.UTC),
    ]


@pytest.mark.django_db
def test_aggregate_datetime_plus_aggregate_duration() -> None:
    models.Event.objects.bulk_create(
        [
            models.Event(
                start=dt.datetime(2015, 8, 16, tzinfo=dt.UTC),
                duration=dt.timedelta(hours=2),
            ),
            models.Event(
                start=dt.datetime(2015, 8, 17, tzinfo=dt.UTC),
                duration=dt.timedelta(hours=1),
            ),
        ]
    )

    result = models.Event.objects.aggregate(
        deadline=aggregates.Max("start") + aggregates.Max("duration")
    )

    assert result == {"deadline": dt.datetime(2015, 8, 17, 2, tzinfo=dt.UTC)}


@pytest.mark.django_db
def test_date_arithmetic_check_constraint_rejects_row() -> None:
    start = dt.datetime(2015, 8, 16, tzinfo=dt.UTC)
    models.Shift.objects.create(
        start=start, end=start + dt.timedelta(hours=1), duration=dt.timedelta(hours=1)
    )

    with pytest.raises(django.db.IntegrityError) as excinfo:
        models.Shift.objects.create(
            start=start,
            end=start + dt.timedelta(hours=3),
            duration=dt.timedelta(hours=2),
        )

    assert str(excinfo.value) == "CHECK constraint failed: shift_ends_within_duration"


@pytest.mark.django_db
def test_date_arithmetic_unique_index_rejects_duplicate() -> None:
    models.Shift.objects.create(
        start=dt.datetime(2015, 8, 16, 1, tzinfo=dt.UTC),
        end=dt.datetime(2015, 8, 16, 2, tzinfo=dt.UTC),
        duration=dt.timedelta(hours=1),
    )

    # Turso's message spells out the whole indexed expression.
    with pytest.raises(django.db.IntegrityError):
        models.Shift.objects.create(
            start=dt.datetime(2015, 8, 16, tzinfo=dt.UTC),
            end=dt.datetime(2015, 8, 16, 2, tzinfo=dt.UTC),
            duration=dt.timedelta(hours=2),
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("connector", "expression"),
    [
        pytest.param(
            "#",
            expressions.CombinedExpression(
                expressions.F("duration"),
                expressions.Combinable.BITXOR,
                expressions.Value(1),
            ),
            id="bitxor",
        ),
        pytest.param(
            "%%",
            expressions.CombinedExpression(
                expressions.F("duration"),
                expressions.Combinable.MOD,
                expressions.Value(2),
                output_field=fields.DurationField(),
            ),
            id="mod",
        ),
    ],
)
def test_duration_with_invalid_connector_raises_database_error(
    connector: str, expression: expressions.CombinedExpression
) -> None:
    queryset = models.Event.objects.values_list(expression, flat=True)

    with pytest.raises(django.db.DatabaseError) as excinfo:
        list(queryset)

    assert str(excinfo.value) == f"Invalid connector for timedelta: {connector}."
