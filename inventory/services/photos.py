"""Photo storage: validate, resize, convert and serve SKU photos.

Files live under ``data/media/sku_photos/<SKU>/<random>.png`` — the same
folder layout the PHP app used, so old photo folders can be copied in as-is.
"""
from __future__ import annotations

import logging
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from PIL import Image, ImageOps, UnidentifiedImageError

from core.skus import normalize_sku, sanitize_filename, sku_directory
from inventory.models import ItemEvent, Photo
from inventory.services import history

logger = logging.getLogger("pinksheet")

ALLOWED_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "GIF": "image/gif"}
EXTENSION_FOR_MIME = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
MIME_FOR_EXTENSION = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif"}
THUMB_SIZE = 320


class PhotoError(Exception):
    """A photo was rejected; the message is safe to show to the operator."""


@dataclass
class StoredImage:
    stored_name: str
    mime_type: str
    file_size: int


def photo_root() -> Path:
    return Path(settings.MEDIA_ROOT) / "sku_photos"


def photo_path(photo: Photo) -> Path:
    return photo_root() / sku_directory(photo.sku_normalized) / Path(photo.stored_name).name


def _open_image(source) -> Image.Image:
    try:
        image = Image.open(source)
        image.load()
    except Image.DecompressionBombError as exc:
        raise PhotoError("That image is far too large to process.") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise PhotoError("is not a valid image file") from exc
    if image.format not in ALLOWED_FORMATS:
        raise PhotoError("must be JPG, PNG, WebP, or GIF")
    return image


def store_image(source, dest_dir: Path) -> StoredImage:
    """Decode an upload, fix phone rotation, shrink it and write it into ``dest_dir``.

    Decoding every upload with Pillow (instead of trusting the file name or
    browser MIME type) is what stops non-images from being stored.
    """
    config = settings.PINKSHEET
    image = _open_image(source)
    source_format = image.format
    image = ImageOps.exif_transpose(image)  # also drops EXIF (GPS etc.)
    if image.mode not in ("RGB", "RGBA", "L", "LA"):
        has_alpha = "transparency" in image.info or image.mode in ("PA",)
        image = image.convert("RGBA" if has_alpha else "RGB")
    max_dim = config["PHOTO_MAX_DIMENSION"]
    if max(image.size) > max_dim:
        image.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

    dest_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    if config["PHOTO_CONVERT_TO_PNG"] or source_format == "PNG":
        name, mime = f"{token}.png", "image/png"
        target = dest_dir / name
        image.save(target, "PNG", optimize=True)
    elif source_format == "GIF":
        name, mime = f"{token}.gif", "image/gif"
        target = dest_dir / name
        image.save(target, "GIF")
    elif source_format == "WEBP":
        name, mime = f"{token}.webp", "image/webp"
        target = dest_dir / name
        image.save(target, "WEBP", quality=80)
    else:
        name, mime = f"{token}.jpg", "image/jpeg"
        target = dest_dir / name
        image.convert("RGB").save(target, "JPEG", quality=85, optimize=True, progressive=True)
    return StoredImage(stored_name=name, mime_type=mime, file_size=target.stat().st_size)


def save_sku_photo(sku: str, upload, original_name: str | None = None) -> Photo:
    """Validate an uploaded file and attach it to ``sku`` as the last photo."""
    sku_norm = normalize_sku(sku)
    if not sku_norm:
        raise PhotoError("Enter a SKU before uploading photos so they can attach.")
    display_name = sanitize_filename(original_name or getattr(upload, "name", "") or "photo")
    size = getattr(upload, "size", None)
    if size is not None and size > settings.PINKSHEET["PHOTO_MAX_BYTES"]:
        limit_mb = settings.PINKSHEET["PHOTO_MAX_BYTES"] // (1024 * 1024)
        raise PhotoError(f"{display_name} is larger than the {limit_mb} MB limit.")
    try:
        stored = store_image(upload, photo_root() / sku_directory(sku_norm))
    except PhotoError as exc:
        raise PhotoError(f"{display_name} {exc}".strip()) from exc

    with transaction.atomic():
        next_sort = (Photo.objects.filter(sku_normalized=sku_norm).aggregate(m=Max("sort_order"))["m"] or 0) + 1
        photo = Photo.objects.create(
            sku_normalized=sku_norm,
            original_name=display_name,
            stored_name=stored.stored_name,
            mime_type=stored.mime_type,
            file_size=stored.file_size,
            sort_order=next_sort,
        )
        history.record_photo(sku_norm, ItemEvent.Action.PHOTO_ADDED)
        return photo


def delete_photo(photo: Photo) -> None:
    path = photo_path(photo)
    photo_id = photo.pk
    photo.delete()
    history.record_photo(photo.sku_normalized, ItemEvent.Action.PHOTO_REMOVED)
    for candidate in [path, *thumb_dir().glob(f"{photo_id}-*")]:
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not delete photo file %s", candidate)


def set_thumbnail(sku: str, photo_id: int) -> bool:
    sku_norm = normalize_sku(sku)
    with transaction.atomic():
        if not Photo.objects.filter(pk=photo_id, sku_normalized=sku_norm).exists():
            return False
        Photo.objects.filter(sku_normalized=sku_norm).update(is_thumb=False)
        Photo.objects.filter(pk=photo_id).update(is_thumb=True)
    return True


def reorder(photo_ids: list[int]) -> None:
    with transaction.atomic():
        for position, photo_id in enumerate(photo_ids, start=1):
            Photo.objects.filter(pk=photo_id).update(sort_order=position)


def preferred_photo(sku: str) -> Photo | None:
    """The photo shown in lists: the chosen thumbnail, else the newest photo."""
    return Photo.objects.filter(sku_normalized=normalize_sku(sku)).order_by("-is_thumb", "-id").first()


def thumbnail_map(skus) -> dict[str, int]:
    """{normalized SKU: preferred photo id} for many SKUs in one query."""
    norms = {normalize_sku(s) for s in skus if s}
    result: dict[str, int] = {}
    if not norms:
        return result
    rows = Photo.objects.filter(sku_normalized__in=norms).order_by("-is_thumb", "-id").values_list("sku_normalized", "id")
    for sku_norm, photo_id in rows:
        result.setdefault(sku_norm, photo_id)
    return result


def move_photos(old_sku: str, new_sku: str) -> int:
    """Re-key photos when an item's SKU is renamed, moving the files too."""
    old_norm, new_norm = normalize_sku(old_sku), normalize_sku(new_sku)
    if not old_norm or not new_norm or old_norm == new_norm:
        return 0
    old_dir = photo_root() / sku_directory(old_norm)
    new_dir = photo_root() / sku_directory(new_norm)
    moved = 0
    for photo in Photo.objects.filter(sku_normalized=old_norm):
        source = old_dir / photo.stored_name
        if source.exists():
            new_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(new_dir / photo.stored_name))
        photo.sku_normalized = new_norm
        photo.save(update_fields=["sku_normalized"])
        moved += 1
    return moved


# ── Thumbnails ──────────────────────────────────────────────────────────────
def thumb_dir() -> Path:
    path = Path(settings.MEDIA_ROOT) / "thumbs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def thumbnail_file(photo: Photo, size: int = THUMB_SIZE) -> Path | None:
    """A cached small JPEG for fast grids; rebuilt automatically if the photo changes."""
    source = photo_path(photo)
    if not source.exists():
        return None
    target = thumb_dir() / f"{photo.pk}-{int(source.stat().st_mtime)}-t{size}.jpg"
    if target.exists():
        return target
    try:
        with Image.open(source) as image:
            image.load()
            image = ImageOps.exif_transpose(image)
            image.thumbnail((size, size), Image.Resampling.LANCZOS)
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGBA")
                background = Image.new("RGB", image.size, (255, 255, 255))
                background.paste(image, mask=image.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")
            image.save(target, "JPEG", quality=82, optimize=True)
    except (OSError, ValueError, Image.DecompressionBombError):
        logger.warning("Could not build thumbnail for photo %s", photo.pk)
        return None
    return target
