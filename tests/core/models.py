from django.db import models
from django.db.models import expressions, lookups


class Attachment(models.Model):
    payload = models.BinaryField()

    def __str__(self) -> str:
        return f"attachment {self.pk}"


class Tag(models.Model):
    name = models.TextField()

    def __str__(self) -> str:
        return self.name


class Event(models.Model):
    start = models.DateTimeField(null=True)
    end = models.DateTimeField(null=True)
    day = models.DateField(null=True)
    time = models.TimeField(null=True)
    duration = models.DurationField(null=True)

    def __str__(self) -> str:
        return f"event {self.pk}"


class Product(models.Model):
    name = models.CharField(max_length=30)

    class Meta:
        indexes = [models.Index(fields=["name"], name="ProductNameIndex")]

    def __str__(self) -> str:
        return self.name


class Counter(models.Model):
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

    def __str__(self) -> str:
        return f"counter {self.pk}"
