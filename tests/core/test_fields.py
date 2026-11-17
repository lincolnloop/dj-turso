import pytest

from tests.core import models


@pytest.mark.django_db
def test_binary_field_round_trips_bytes() -> None:
    attachment = models.Attachment.objects.create(payload=b"\x00\x01")

    attachment.refresh_from_db()

    assert bytes(attachment.payload) == b"\x00\x01"
