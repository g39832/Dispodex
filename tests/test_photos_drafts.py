import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from inventory.models import IntakeDraft, Photo
from inventory.services import photos as photo_service

pytestmark = pytest.mark.django_db(transaction=True)


def upload(client, sku, file):
    return client.post(reverse("api_photo_upload"), {"sku": sku, "photo": file})


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP", "GIF"])
def test_upload_converts_every_format_to_png(client, image, fmt):
    response = upload(client, "SKU-1", image(fmt))
    assert response.status_code == 200, response.content
    photo = Photo.objects.get()
    assert photo.mime_type == "image/png"
    path = photo_service.photo_path(photo)
    assert path.exists() and path.parent.name == "SKU-1"
    with Image.open(path) as stored:
        assert stored.format == "PNG"


def test_big_photos_are_shrunk(client, image):
    upload(client, "SKU-1", image(size=(3000, 1500)))
    with Image.open(photo_service.photo_path(Photo.objects.get())) as stored:
        assert max(stored.size) == 1200


def test_non_images_are_rejected(client):
    fake = SimpleUploadedFile("evil.jpg", b"<?php echo 'hi'; ?>", content_type="image/jpeg")
    response = upload(client, "SKU-1", fake)
    assert response.status_code == 400
    assert "not a valid image" in response.json()["error"]
    assert not Photo.objects.exists()


def test_upload_needs_a_sku(client, image):
    response = upload(client, "", image())
    assert response.status_code == 400


def test_serve_photo_thumbnail_and_download(client, image):
    upload(client, "SKU-1", image(name="front.jpg"))
    photo = Photo.objects.get()
    full = client.get(reverse("photo", args=[photo.pk]))
    assert full.status_code == 200 and full["Content-Type"] == "image/png"
    thumb = client.get(reverse("photo", args=[photo.pk]) + "?thumb=1")
    assert thumb["Content-Type"] == "image/jpeg"
    download = client.get(reverse("photo", args=[photo.pk]) + "?download=1")
    assert "attachment" in download["Content-Disposition"]
    assert "SKU-1_front.png" in download["Content-Disposition"]
    etag = full["ETag"]
    again = client.get(reverse("photo", args=[photo.pk]), HTTP_IF_NONE_MATCH=etag)
    assert again.status_code == 304


def test_thumbnail_reorder_and_delete(client, image):
    for _ in range(3):
        upload(client, "SKU-1", image())
    ids = list(Photo.objects.values_list("id", flat=True))
    assert client.post(reverse("api_photo_thumbnail", args=[ids[1]])).json()["ok"]
    assert Photo.objects.get(pk=ids[1]).is_thumb
    assert photo_service.preferred_photo("SKU-1").pk == ids[1]

    client.post(reverse("api_photo_reorder"), {"ids": list(reversed(ids))}, content_type="application/json")
    assert list(Photo.objects.filter(sku_normalized="SKU-1").values_list("id", flat=True)) == list(reversed(ids))

    path = photo_service.photo_path(Photo.objects.get(pk=ids[0]))
    assert client.post(reverse("api_photo_delete", args=[ids[0]])).json()["ok"]
    assert not path.exists()
    assert Photo.objects.count() == 2


def test_photo_actions_blocked_from_public_internet(image):
    from django.test import Client

    Client(REMOTE_ADDR="127.0.0.1").post(reverse("api_photo_upload"), {"sku": "SKU-1", "photo": image()})
    photo = Photo.objects.get()
    outsider = Client(REMOTE_ADDR="8.8.8.8")
    assert outsider.post(reverse("api_photo_delete", args=[photo.pk])).status_code == 403


# ── autosave drafts ──────────────────────────────────────────────────────────
def save_draft(client, sku, data, version=None, client_id="A", force=False):
    return client.post(reverse("api_draft", args=[sku]),
                       {"data": data, "version": version, "client": client_id, "force": force},
                       content_type="application/json")


def test_draft_round_trip(client):
    response = save_draft(client, "d-1", {"what_is_it": "Laptop", "unknown_field": "dropped"})
    assert response.json()["version"] == 1
    loaded = client.get(reverse("api_draft", args=["D-1"])).json()
    assert loaded["has_draft"] and loaded["data"]["what_is_it"] == "Laptop"
    assert "unknown_field" not in loaded["data"]


def test_same_client_can_keep_saving(client):
    v1 = save_draft(client, "D-1", {"notes": "a"}).json()["version"]
    v2 = save_draft(client, "D-1", {"notes": "b"}, version=v1).json()["version"]
    assert v2 == 2


def test_conflict_when_someone_else_saved_newer(client):
    save_draft(client, "D-1", {"notes": "mine"}, client_id="A")
    response = save_draft(client, "D-1", {"notes": "theirs"}, version=None, client_id="B")
    assert response.status_code == 409
    assert response.json()["data"]["notes"] == "mine"
    forced = save_draft(client, "D-1", {"notes": "theirs"}, client_id="B", force=True)
    assert forced.json()["ok"]
    assert IntakeDraft.objects.get().payload["notes"] == "theirs"


def test_draft_rejects_oversized_payload(client):
    response = save_draft(client, "D-1", {"notes": "x" * 70000})
    assert response.status_code == 400


def test_discard_draft(client):
    save_draft(client, "D-1", {"notes": "a"})
    client.delete(reverse("api_draft", args=["D-1"]))
    assert not IntakeDraft.objects.exists()
