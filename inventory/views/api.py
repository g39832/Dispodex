"""JSON endpoints used by the pages' JavaScript. Every answer has an ``ok`` flag."""
from __future__ import annotations

import logging
import math
from functools import wraps
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.http import json_error, json_ok, read_json_body
from core.network import not_in_demo, private_network_only
from core.skus import normalize_sku, sku_directory
from inventory.models import Item, ListingImageLayout, Photo, Status
from inventory.services import drafts as draft_service
from inventory.services import ebay_categories, labels, scripts
from inventory.services import items as item_service
from inventory.services import photos as photo_service
from inventory.services import search
from squaresync import queue as square_queue

logger = logging.getLogger("pinksheet")


def _item_json(item: Item) -> dict:
    return {
        "id": item.pk,
        "sku": item.sku,
        "sku_normalized": item.sku_normalized,
        "status": item.status,
        "status_label": item.status_label,
        "price": None if item.price is None else f"{item.price:.2f}",
        "quantity": item.quantity,
        "reviewed": item.reviewed,
        "ready": item.ready,
        "notes": item.notes,
        "updated_at": timezone.localtime(item.updated_at).strftime("%Y-%m-%d %H:%M"),
    }


# ── items ────────────────────────────────────────────────────────────────────
@require_GET
def item_copy(request, sku):
    data = item_service.copy_fields(sku)
    if data is None:
        return json_error("No record for that SKU.", 404)
    return json_ok(data=data)


@require_POST
def item_update(request, sku):
    body = read_json_body(request) or request.POST
    try:
        item = item_service.quick_update(sku, str(body.get("field") or ""), body.get("value"))
    except item_service.ItemError as exc:
        return json_error(str(exc), 404 if "not found" in str(exc).lower() else 400)
    return json_ok(item=_item_json(item))


@require_POST
def item_bulk_update(request):
    body = read_json_body(request) or {}
    try:
        updated, failed = item_service.bulk_update(body.get("skus"), str(body.get("field") or ""), body.get("value"))
    except item_service.ItemError as exc:
        return json_error(str(exc))
    return json_ok(updated=len(updated), failed=failed)


@require_POST
def item_delete(request, item_id):
    body = read_json_body(request) or request.POST
    if str(body.get("confirm") or "").strip().upper() != "DELETE":
        return json_error("Type DELETE to confirm.")
    try:
        item = item_service.soft_delete(item_id)
    except item_service.ItemError as exc:
        return json_error(str(exc), 404)
    return json_ok(deleted=item.pk, sku=item.sku)


@require_POST
def item_undo_delete(request):
    try:
        item = item_service.undo_last_delete()
    except item_service.ItemError as exc:
        return json_error(str(exc), 409)
    return json_ok(item=_item_json(item))


# ── autosave drafts ──────────────────────────────────────────────────────────
@require_http_methods(["GET", "POST", "DELETE"])
def draft(request, sku):
    if request.method == "GET":
        existing = draft_service.get_draft(sku)
        if existing is None:
            return json_ok(has_draft=False)
        return json_ok(has_draft=True, version=existing.version, data=existing.payload,
                       updated_at=existing.updated_at.isoformat())
    if request.method == "DELETE":
        draft_service.discard_draft(sku)
        return json_ok()
    body = read_json_body(request)
    try:
        version = body.get("version")
        result = draft_service.save_draft(
            sku,
            body.get("data"),
            int(version) if version not in (None, "") else None,
            str(body.get("client") or ""),
            force=bool(body.get("force")),
        )
    except (draft_service.DraftError, ValueError) as exc:
        return json_error(str(exc))
    if not result.ok:
        return json_error("Someone else has newer unsaved changes for this SKU.", 409,
                          version=result.version, data=result.conflict_payload)
    return json_ok(version=result.version, saved_at=timezone.now().isoformat())


# ── search ───────────────────────────────────────────────────────────────────
@require_GET
def suggestions(request):
    return json_ok(results=search.suggestions(request.GET.get("q", "")))


@require_GET
def palette(request):
    if request.GET.get("recent") == "1":
        return json_ok(results=search.palette_recent())
    return json_ok(results=search.palette_search(request.GET.get("q", "")))


# The SOLD lane only needs recent sales; the rest are one click away in Lookup.
SOLD_ON_BOARD = 150


@require_GET
def board_cards(request):
    live = Item.objects.exclude(sku_normalized="").order_by("-updated_at", "-id")
    sold = live.filter(status=Status.SOLD)
    sold_total = sold.count()
    # Every item that isn't sold is always on the board, however many there are.
    items = list(live.exclude(status=Status.SOLD)) + list(sold[:SOLD_ON_BOARD])
    thumbs = photo_service.thumbnail_map(i.sku_normalized for i in items)
    lanes = {value: [] for value in Status.values}
    for item in items:
        lanes[item.status].append(
            {
                "id": item.pk,
                "sku": item.sku,
                "norm": item.sku_normalized,
                "what": item.what_is_it,
                "brand": item.brand_model,
                "condition": item.condition,
                "updated": timezone.localtime(item.updated_at).strftime("%b %d, %H:%M"),
                "price": None if item.price is None else f"{item.price:.2f}",
                "reviewed": item.reviewed,
                "ready": item.ready,
                "qty": item.quantity,
                "thumb_id": thumbs.get(item.sku_normalized),
                "qr_url": request.build_absolute_uri(reverse("card", args=[item.sku_normalized])),
            }
        )
    more = {Status.SOLD.value: max(0, sold_total - SOLD_ON_BOARD)}
    return json_ok(lanes=lanes, more=more, totals={Status.SOLD.value: sold_total})


# ── photos ───────────────────────────────────────────────────────────────────
def _photo_json(photo: Photo) -> dict:
    return {
        "id": photo.pk,
        "name": photo.original_name,
        "size": photo.file_size,
        "is_thumb": photo.is_thumb,
        "low_res": photo.low_res,
        "mime": photo.mime_type or "image/png",
        "url": reverse("photo", args=[photo.pk]),
        "thumb_url": reverse("photo", args=[photo.pk]) + "?thumb=wide",
    }


@require_GET
def photo_list(request):
    sku = normalize_sku(request.GET.get("sku"))
    return json_ok(photos=[_photo_json(p) for p in Photo.objects.filter(sku_normalized=sku)] if sku else [])


@require_POST
@not_in_demo
def photo_upload(request):
    sku = normalize_sku(request.POST.get("sku"))
    files = request.FILES.getlist("photo") or request.FILES.getlist("photos")
    if not files:
        return json_error("No photo was uploaded.")
    saved, errors = [], []
    for upload in files:
        try:
            saved.append(photo_service.save_sku_photo(sku, upload))
        except photo_service.PhotoError as exc:
            errors.append(str(exc))
    if saved and Item.objects.filter(sku_normalized=sku).exists():
        square_queue.enqueue(sku)
    if not saved:
        return json_error(errors[0] if errors else "Upload failed.")
    return json_ok(photos=[_photo_json(p) for p in saved], errors=errors)


@require_POST
@private_network_only
def photo_delete(request, photo_id):
    photo = Photo.objects.filter(pk=photo_id).first()
    if photo is None:
        return json_error("That photo no longer exists.", 404)
    sku = photo.sku_normalized
    photo_service.delete_photo(photo)
    square_queue.enqueue(sku)
    return json_ok()


@require_POST
@private_network_only
def photo_set_thumbnail(request, photo_id):
    photo = Photo.objects.filter(pk=photo_id).first()
    if photo is None or not photo_service.set_thumbnail(photo.sku_normalized, photo.pk):
        return json_error("Photo not found for that SKU.", 404)
    square_queue.enqueue(photo.sku_normalized)
    return json_ok()


@require_POST
@private_network_only
def photo_reorder(request):
    body = read_json_body(request)
    try:
        ids = [int(i) for i in body.get("ids") or []]
    except (TypeError, ValueError):
        return json_error("Photo ids must be numbers.")
    if len(ids) < 2:
        return json_error("At least two photos are needed to reorder.")
    photo_service.reorder(ids)
    return json_ok()


# ── script builder ───────────────────────────────────────────────────────────
@require_http_methods(["GET", "POST"])
def script(request, sku):
    if request.method == "GET":
        return json_ok(**scripts.load_script(sku, fresh_prompt=request.GET.get("fresh") == "1"))
    body = read_json_body(request)
    if not normalize_sku(sku):
        return json_error("SKU is required.")
    cache = scripts.save_script(
        sku, str(body.get("prompt_text") or ""), str(body.get("chatgpt_text") or ""),
        str(body.get("final_text") or ""), str(body.get("sku_display") or ""),
    )
    return json_ok(saved_at=timezone.localtime(cache.updated_at).strftime("%H:%M:%S"), state=cache.state)


@require_POST
def script_build(request):
    """Turn a pasted ChatGPT answer into the eBay title, final description and notes for staff."""
    body = read_json_body(request)
    return json_ok(**scripts.build_listing(str(body.get("chatgpt_text") or "")))


# ── labels & categories ──────────────────────────────────────────────────────
@require_GET
def label_zpl(request):
    sku = (request.GET.get("sku") or "").strip()
    if not sku:
        return json_error("Enter a SKU before printing.")
    item = Item.objects.filter(sku_normalized=normalize_sku(sku)).first()
    try:
        dpi = int(request.GET.get("dpi") or 203)
        font = int(request.GET.get("font_size") or 0)
    except ValueError:
        dpi, font = 203, 0
    details = request.GET.get("show_details")
    payload = labels.label_for_item(
        sku, item, preset=request.GET.get("preset") or "compact", dpi=dpi,
        code_type=request.GET.get("code_type") or "", font_size=font,
        show_details=None if details in (None, "") else details == "1",
    )
    response = json_ok(**payload)
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def ebay_category_list(request):
    data = ebay_categories.category_list()
    return json_ok(count=len(data["categories"]), **data)


# ── eBay listing-image composer ──────────────────────────────────────────────
def listing_images_on(view):
    """The composer is unfinished and hidden (PINKSHEET_LISTING_IMAGES=0): refuse its actions too."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not settings.PINKSHEET["LISTING_IMAGES"]:
            return json_error("Listing images is turned off for now.", 404)
        return view(request, *args, **kwargs)

    return wrapper


@require_POST
@listing_images_on
@not_in_demo
def listing_image_upload(request):
    sku = normalize_sku(request.POST.get("sku"))
    upload = request.FILES.get("photo")
    if not sku:
        return json_error("Select a SKU first.")
    if upload is None:
        return json_error("No file uploaded.")
    if upload.size > settings.PINKSHEET["PHOTO_MAX_BYTES"]:
        return json_error(f"{upload.name} is too large.")
    folder = Path(settings.MEDIA_ROOT) / "ebay_images" / sku_directory(sku)
    try:
        stored = photo_service.store_image(upload, folder, widen_to=photo_service.min_width())
    except photo_service.PhotoError as exc:
        return json_error(f"{upload.name} {exc}")
    return json_ok(
        id=stored.stored_name,
        url=reverse("listing_image_file", args=[sku, stored.stored_name]),
        name=Path(upload.name).stem + Path(stored.stored_name).suffix,
        size=stored.file_size,
    )


@require_POST
@listing_images_on
def listing_image_layout(request, sku):
    sku = normalize_sku(sku)
    body = read_json_body(request)
    positions = body.get("positions")
    if not sku:
        return json_error("SKU is required.")
    if not isinstance(positions, list):
        return json_error("positions must be a list.")
    clean = []
    for entry in positions[:200]:
        if not isinstance(entry, dict):
            continue
        try:
            x, y = float(entry.get("x") or 0), float(entry.get("y") or 0)
        except (TypeError, ValueError):
            return json_error("Positions must be numbers.")
        if not (math.isfinite(x) and math.isfinite(y)):
            return json_error("Positions must be numbers.")
        clean.append(
            {
                "id": entry.get("id"),
                "src": str(entry.get("src") or "")[:2048],
                "name": str(entry.get("name") or "")[:255],
                "x": x,
                "y": y,
            }
        )
    with transaction.atomic():
        ListingImageLayout.objects.update_or_create(
            sku_normalized=sku, defaults={"positions": clean, "updated_at": timezone.now()}
        )
    return json_ok(count=len(clean))
