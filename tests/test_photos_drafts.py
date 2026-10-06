import io

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
    upload(client, "SKU-1", image(size=(6000, 3000)))
    with Image.open(photo_service.photo_path(Photo.objects.get())) as stored:
        assert max(stored.size) == 3000


def test_narrow_photos_are_widened_for_ebay(client, image):
    upload(client, "SKU-1", image(size=(240, 320)))
    upload(client, "SKU-1", image(size=(800, 600)))
    narrow, wide = Photo.objects.order_by("id")
    with Image.open(photo_service.photo_path(narrow)) as stored:
        assert stored.size == (500, 667)
    with Image.open(photo_service.photo_path(wide)) as stored:
        assert stored.size == (800, 600)


def test_existing_small_photos_are_widened_and_originals_kept(settings, image):
    small = photo_service.save_sku_photo("SKU-1", image(size=(320, 240)))
    settings.PINKSHEET = {**settings.PINKSHEET, "PHOTO_MIN_WIDTH": 0}
    tiny = photo_service.save_sku_photo("SKU-1", image("JPEG", size=(100, 50)))
    settings.PINKSHEET = {**settings.PINKSHEET, "PHOTO_MIN_WIDTH": 500}
    original = photo_service.photo_path(tiny)
    assert photo_service.photo_path(small) != original

    result = photo_service.widen_small_photos()
    assert result.widened == 1 and result.failed == 0
    tiny.refresh_from_db()
    with Image.open(photo_service.photo_path(tiny)) as stored:
        assert stored.size == (500, 250)
    assert original.exists()  # an older database backup still finds its photo
    assert photo_service.widen_small_photos().widened == 0  # already done


def test_photo_grids_and_full_photos_are_never_under_500px(client, settings, image):
    settings.PINKSHEET = {**settings.PINKSHEET, "PHOTO_MIN_WIDTH": 0}
    photo = photo_service.save_sku_photo("SKU-1", image(size=(240, 320)))  # like an old, not-yet-widened photo
    settings.PINKSHEET = {**settings.PINKSHEET, "PHOTO_MIN_WIDTH": 500}
    url = reverse("photo", args=[photo.pk])

    grid = client.get(url + "?thumb=wide")
    with Image.open(io.BytesIO(b"".join(grid.streaming_content))) as shown:
        assert shown.size == (500, 667)
    with Image.open(io.BytesIO(b"".join(client.get(url + "?thumb=1").streaming_content))) as small:
        assert small.size == (240, 320)  # list thumbnails stay small and fast

    full = client.get(url + "?download=1")
    with Image.open(io.BytesIO(b"".join(full.streaming_content))) as saved:
        assert saved.size == (500, 667)
    photo.refresh_from_db()
    with Image.open(photo_service.photo_path(photo)) as stored:
        assert stored.width == 500
    assert "?thumb=wide" in client.get(reverse("intake") + "?sku=SKU-1").content.decode()


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


# ── photo quality and dragging photos out to eBay ────────────────────────────
def test_big_uploads_stay_sharp_for_ebay_zoom(client, image):
    upload(client, "SKU-1", image(size=(4000, 3000)))
    with Image.open(photo_service.photo_path(Photo.objects.get())) as stored:
        assert stored.size == (3000, 2250)  # well past eBay's 1600px zoom size


def test_photos_already_under_the_cap_keep_every_pixel(client, image):
    upload(client, "SKU-1", image(size=(2400, 1800)))
    with Image.open(photo_service.photo_path(Photo.objects.get())) as stored:
        assert stored.size == (2400, 1800)


def test_stored_photos_never_pass_ebays_12mb_limit(client, settings, image, monkeypatch):
    monkeypatch.setattr(photo_service, "EBAY_MAX_BYTES", 1024 * 1024)
    noise = Image.effect_noise((1500, 1500), 100).convert("RGB")  # detailed, so PNG compresses badly
    buffer = io.BytesIO()
    noise.save(buffer, "PNG")
    assert len(buffer.getvalue()) > 1024 * 1024
    upload(client, "SKU-1", SimpleUploadedFile("noisy.png", buffer.getvalue(), content_type="image/png"))
    photo = Photo.objects.get()
    assert photo_service.photo_path(photo).stat().st_size <= 1024 * 1024
    assert photo.file_size <= 1024 * 1024
    with Image.open(photo_service.photo_path(photo)) as stored:
        assert stored.format == "PNG" and stored.width >= 500


def test_jpg_and_webp_are_kept_at_high_quality_when_not_converted(client, settings, image):
    settings.PINKSHEET = {**settings.PINKSHEET, "PHOTO_CONVERT_TO_PNG": False}
    original = Image.effect_noise((800, 600), 60).convert("RGB")
    buffer = io.BytesIO()
    original.save(buffer, "JPEG", quality=100)
    upload(client, "SKU-1", SimpleUploadedFile("p.jpg", buffer.getvalue(), content_type="image/jpeg"))
    with Image.open(photo_service.photo_path(Photo.objects.get())) as stored:
        assert stored.format == "JPEG"
        # Pillow reports the quantization tables; quality 95 keeps the luma table's values tiny.
        assert max(stored.quantization[0]) <= 12  # quality 85 would be 28+


def test_grid_previews_are_high_quality_and_rebuilt_after_the_change(client, image):
    upload(client, "SKU-1", image(size=(1600, 1200)))
    photo = Photo.objects.get()
    thumb = photo_service.thumbnail_file(photo, width=photo_service.GRID_PREVIEW_WIDTH)
    assert thumb.name.endswith("-q95.jpg")  # older, blurrier cached previews are not reused
    with Image.open(thumb) as shown:
        assert shown.width == 1000
        assert max(shown.quantization[0]) <= 12  # quality 85 would be 28+
    response = client.get(reverse("photo", args=[photo.pk]) + "?thumb=wide")
    with Image.open(io.BytesIO(b"".join(response.streaming_content))) as shown:
        assert shown.width == 1000
    with Image.open(photo_service.thumbnail_file(photo)) as listed:
        assert max(listed.size) == 640  # list thumbnails stay crisp on high-DPI screens


def test_grid_previews_never_enlarge_a_photo_past_its_own_width(client, image):
    upload(client, "SKU-1", image(size=(700, 500)))
    response = client.get(reverse("photo", args=[Photo.objects.get().pk]) + "?thumb=wide")
    with Image.open(io.BytesIO(b"".join(response.streaming_content))) as shown:
        assert shown.size == (700, 500)


def test_photo_tiles_say_what_kind_of_file_they_are_for_dragging(client, image):
    from inventory.models import Item

    Item.objects.create(sku="SKU-1", what_is_it="Laptop")
    upload(client, "SKU-1", image())
    photo = Photo.objects.get()
    listed = client.get(reverse("api_photo_list") + "?sku=SKU-1").json()["photos"][0]
    assert listed["mime"] == photo.mime_type and listed["url"] == f"/photos/{photo.pk}/"
    html = client.get(reverse("intake") + "?sku=SKU-1").content.decode()
    assert f'data-photo-id="{photo.pk}" data-mime="{photo.mime_type}"' in html


def test_dragged_photo_link_serves_the_full_photo(client, image):
    upload(client, "SKU-1", image(size=(4000, 3000)))
    photo = Photo.objects.get()
    response = client.get(f"/photos/{photo.pk}/")
    with Image.open(io.BytesIO(b"".join(response.streaming_content))) as full:
        assert full.size == (3000, 2250)
    download = client.get(f"/photos/{photo.pk}/?download=1")
    assert download["Content-Disposition"].startswith("attachment;")


def _phone_photo(fmt, ext, mime):
    buffer = io.BytesIO()
    main = Image.new("RGB", (800, 600), (58, 120, 194))
    if fmt == "MPO":  # a JPEG with an extra embedded image, as iPhone HDR and Samsung photos are
        main.save(buffer, "MPO", save_all=True, append_images=[Image.new("RGB", (200, 150))])
    else:
        main.save(buffer, fmt)
    return SimpleUploadedFile(f"IMG_0001.{ext}", buffer.getvalue(), content_type=mime)


@pytest.mark.parametrize("fmt, ext, mime", [("MPO", "jpg", "image/jpeg"), ("HEIF", "heic", "image/heic")])
@pytest.mark.parametrize("to_png", [True, False])
def test_phone_photos_are_accepted(client, settings, fmt, ext, mime, to_png):
    settings.PINKSHEET = {**settings.PINKSHEET, "PHOTO_CONVERT_TO_PNG": to_png}
    response = upload(client, "SKU-1", _phone_photo(fmt, ext, mime))
    assert response.status_code == 200, response.content
    with Image.open(photo_service.photo_path(Photo.objects.get())) as stored:
        assert stored.format == ("PNG" if to_png else "JPEG")
