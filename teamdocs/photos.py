"""Photo files for team docs, stored and checked the same way as item photos."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from django.conf import settings
from django.db.models import Max

from core.skus import sanitize_filename
from inventory.services.photos import PhotoError, store_image
from teamdocs.models import DocPhoto, DocPost

logger = logging.getLogger("pinksheet")

MAX_PHOTOS_PER_POST = 30


def post_dir(post_id: int) -> Path:
    return Path(settings.MEDIA_ROOT) / "doc_photos" / str(int(post_id))


def photo_path(photo: DocPhoto) -> Path:
    return post_dir(photo.post_id) / Path(photo.stored_name).name


def add_photos(post: DocPost, uploads, added_by: str) -> list[DocPhoto]:
    """Store every upload under ``post``; if any is rejected, none are kept.

    Call inside a transaction so a rejected photo also rolls back the rows.
    """
    existing = post.photos.count()
    if existing + len(uploads) > MAX_PHOTOS_PER_POST:
        raise PhotoError(f"A post can have up to {MAX_PHOTOS_PER_POST} photos.")
    limit = settings.PINKSHEET["PHOTO_MAX_BYTES"]
    next_sort = (post.photos.aggregate(m=Max("sort_order"))["m"] or 0) + 1
    written: list[Path] = []
    created: list[DocPhoto] = []
    try:
        for offset, upload in enumerate(uploads):
            name = sanitize_filename(getattr(upload, "name", "") or "photo")
            if (upload.size or 0) > limit:
                raise PhotoError(f"{name} is larger than the {limit // (1024 * 1024)} MB limit.")
            try:
                stored = store_image(upload, post_dir(post.pk))
            except PhotoError as exc:
                raise PhotoError(f"{name} {exc}".strip()) from exc
            written.append(post_dir(post.pk) / stored.stored_name)
            created.append(DocPhoto.objects.create(
                post=post, original_name=name, stored_name=stored.stored_name, mime_type=stored.mime_type,
                file_size=stored.file_size, sort_order=next_sort + offset, added_by=added_by,
            ))
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return created


def delete_photo_file(photo: DocPhoto) -> None:
    try:
        photo_path(photo).unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not delete team doc photo %s", photo_path(photo))


def delete_post_files(post_id: int) -> None:
    shutil.rmtree(post_dir(post_id), ignore_errors=True)
