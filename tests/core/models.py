from django.db import models


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
