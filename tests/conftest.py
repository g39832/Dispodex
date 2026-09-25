import io
import shutil
from pathlib import Path

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from PIL import Image

from inventory.models import Item


@pytest.fixture(autouse=True)
def clean_media():
    """Every test starts with empty photo/backup folders."""
    yield
    for folder in (Path(settings.MEDIA_ROOT), Path(settings.BACKUP_DIR)):
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)


@pytest.fixture
def client():
    return Client(REMOTE_ADDR="127.0.0.1")


@pytest.fixture
def square_settings(settings):
    """Turn Square on with fake credentials (HTTP is mocked with `responses`)."""
    settings.SQUARE = {
        **settings.SQUARE,
        "ENVIRONMENT": "sandbox",
        "ACCESS_TOKEN": "test-token",
        "LOCATION_ID": "LOC1",
        "MAX_RETRIES": 1,
        "WEBHOOK_SIGNATURE_KEY": "sig-key",
        "WEBHOOK_NOTIFICATION_URL": "https://shop.example.com/webhooks/square/",
    }
    return settings


def make_image(fmt="JPEG", size=(40, 30), color=(58, 120, 194), name=None) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, fmt)
    ext = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif"}[fmt]
    return SimpleUploadedFile(name or f"photo.{ext}", buffer.getvalue(), content_type=f"image/{ext}")


@pytest.fixture
def image():
    return make_image


@pytest.fixture
def item(db):
    return Item.objects.create(sku="abc-1", what_is_it="Laptop", brand_model="Dell 5590", price="120.00")
