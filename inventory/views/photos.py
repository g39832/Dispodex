"""Serve photo files (full size, thumbnail or download) with browser caching."""
import hashlib
import logging
import mimetypes
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponseNotModified
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from core.skus import normalize_sku, sanitize_filename, sku_directory
from inventory.models import Photo
from inventory.services import photos as photo_service

logger = logging.getLogger("pinksheet")


def _cached_file_response(request, path: Path, content_type: str, *, download_name: str = "", max_age: int = 86400):
    stat = path.stat()
    etag = '"' + hashlib.sha1(f"{path.name}-{stat.st_size}-{stat.st_mtime_ns}".encode()).hexdigest()[:20] + '"'
    if request.headers.get("If-None-Match") == etag:
        response = HttpResponseNotModified()
        response["ETag"] = etag
        return response
    response = FileResponse(
        path.open("rb"), content_type=content_type, as_attachment=bool(download_name), filename=download_name or path.name
    )
    response["ETag"] = etag
    response["Cache-Control"] = f"private, max-age={max_age}"
    return response


@require_GET
def serve_photo(request, photo_id: int):
    photo = get_object_or_404(Photo, pk=photo_id)
    if request.GET.get("thumb") in ("1", "wide"):
        # "wide": the photo grids people save pictures from, at least eBay's 500px minimum.
        width = photo_service.min_width() if request.GET.get("thumb") == "wide" else 0
        thumb = photo_service.thumbnail_file(photo, width=width)
        if thumb:
            return _cached_file_response(request, thumb, "image/jpeg", max_age=7 * 86400)
    try:
        # Normally done at start-up; this covers a photo the background pass hasn't reached yet.
        photo_service.widen_photo(photo)
    except (OSError, ValueError, SyntaxError):
        logger.warning("Could not widen photo %s", photo.pk)
    path = photo_service.photo_path(photo)
    if not path.exists():
        raise Http404("Photo file is missing")
    download_name = ""
    if request.GET.get("download") == "1":
        base = sanitize_filename(Path(photo.original_name).stem)
        download_name = f"{photo.sku_normalized}_{base}{path.suffix}"
    content_type = photo.mime_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return _cached_file_response(request, path, content_type, download_name=download_name)


@require_GET
def serve_listing_image(request, sku: str, filename: str):
    folder = Path(settings.MEDIA_ROOT) / "ebay_images" / sku_directory(normalize_sku(sku))
    path = folder / Path(filename).name
    if not path.exists() or not path.is_file():
        raise Http404("Image not found")
    return _cached_file_response(request, path, mimetypes.guess_type(path.name)[0] or "image/png")
