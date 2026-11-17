from django.db import models


class Attachment(models.Model):
    payload = models.BinaryField()

    def __str__(self) -> str:
        return f"attachment {self.pk}"
