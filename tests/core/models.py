from django.db import models


class Attachment(models.Model):
    payload = models.BinaryField()

    def __str__(self) -> str:
        return f"attachment {self.pk}"


class Tag(models.Model):
    name = models.TextField()

    def __str__(self) -> str:
        return self.name
