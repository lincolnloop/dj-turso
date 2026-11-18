from django.db import models
from django.db.models import expressions, lookups


class Attachment(models.Model):  # noqa: DJ008
    payload = models.BinaryField()


class Tag(models.Model):  # noqa: DJ008
    name = models.TextField()


class Event(models.Model):  # noqa: DJ008
    start = models.DateTimeField(null=True)
    end = models.DateTimeField(null=True)
    day = models.DateField(null=True)
    time = models.TimeField(null=True)
    duration = models.DurationField(null=True)


class Product(models.Model):  # noqa: DJ008
    name = models.CharField(max_length=30)

    class Meta:
        indexes = [models.Index(fields=["name"], name="ProductNameIndex")]


class Counter(models.Model):  # noqa: DJ008
    value = models.IntegerField()
    mask = models.IntegerField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=lookups.LessThan(
                    expressions.CombinedExpression(
                        expressions.F("value"),
                        expressions.Combinable.BITXOR,
                        expressions.F("mask"),
                    ),
                    8,
                ),
                name="counter_xor_below_8",
            ),
            models.UniqueConstraint(
                expressions.CombinedExpression(
                    expressions.F("value"),
                    expressions.Combinable.BITXOR,
                    expressions.F("mask"),
                ),
                name="counter_unique_xor",
            ),
        ]


class Shift(models.Model):  # noqa: DJ008
    start = models.DateTimeField()
    end = models.DateTimeField()
    duration = models.DurationField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    end__lte=expressions.F("start") + expressions.F("duration")
                ),
                name="shift_ends_within_duration",
            ),
            models.UniqueConstraint(
                expressions.F("start") + expressions.F("duration"),
                name="shift_unique_deadline",
            ),
        ]


class Slug(models.Model):  # noqa: DJ008
    name = models.TextField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(name__regex=r"^[a-z-]+$"), name="slug_lowercase"
            ),
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(name__regex=r"^draft-"),
                name="slug_unique_draft",
            ),
        ]
