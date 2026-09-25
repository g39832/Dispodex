"""Business rules for creating, editing, deleting and restoring items."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from core.skus import normalize_sku
from inventory.models import IntakeDraft, Item, ItemEvent, Review
from inventory.services import history
from inventory.services import photos as photo_service
from squaresync import queue as square_queue

logger = logging.getLogger("pinksheet")

# Fields a quick edit (board, lookup, phone page) may change.
QUICK_EDIT_FIELDS = {"status", "price", "reviewed", "ready", "quantity", "notes"}

# Fields that are copied by "Copy fields from SKU" (never the SKU, ids or dates).
COPYABLE_FIELDS = [
    "status", "what_is_it", "ebay_category", "ebay_category_path", "ebay_category_id",
    "date_received", "source", "where_it_goes", "functional", "condition",
    "cords_adapters", "keep_items_together", "picture_taken", "power_on",
    "brand_model", "ram", "ssd_gb", "cpu", "os", "compatible_os", "battery_health",
    "graphics_card", "screen_resolution", "diagnostics_test_ran", "wifi_card_installed",
    "price", "quantity", "notes",
]


class ItemError(Exception):
    """A rule was broken; the message is safe to show."""


@dataclass
class SaveResult:
    item: Item
    created: bool


def find_item(sku: str) -> Item | None:
    sku_norm = normalize_sku(sku)
    if not sku_norm:
        return None
    return Item.objects.filter(sku_normalized=sku_norm).first()


def parse_price(value) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip().replace("$", "").replace(",", "")
    if text == "":
        return None
    try:
        price = Decimal(text).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ItemError("Price must be a number.") from exc
    if price < 0:
        raise ItemError("Price cannot be negative.")
    return price


@transaction.atomic
def save_intake(cleaned: dict, item_id: int | None = None) -> SaveResult:
    """Save the intake sheet.

    Same rule as the PHP app: a SKU has one live record. Saving a SKU that
    already exists updates that record instead of creating a duplicate.
    """
    sku_norm = normalize_sku(cleaned.get("sku"))
    if not sku_norm:
        raise ItemError("SKU is required.")

    item = None
    if item_id:
        item = Item.objects.select_for_update().filter(pk=item_id).first()
    if item is None:
        item = Item.objects.select_for_update().filter(sku_normalized=sku_norm).first()
    created = item is None
    if created:
        item = Item()

    clash = Item.objects.filter(sku_normalized=sku_norm).exclude(pk=item.pk).first() if item.pk else None
    if clash:
        raise ItemError(f"SKU {sku_norm} already belongs to another item. Open it from Lookup instead.")

    old_sku = item.sku_normalized if item.pk else ""
    before = history.snapshot(item) if item.pk else None
    new_status = cleaned.pop("status", None) or item.status
    for field, value in cleaned.items():
        setattr(item, field, value)
    item.apply_status(new_status)
    item.save()
    if created:
        history.record(item, ItemEvent.Action.CREATED)
    else:
        history.record(item, ItemEvent.Action.EDITED, before=before)

    if old_sku and old_sku != item.sku_normalized:
        photo_service.move_photos(old_sku, item.sku_normalized)
    IntakeDraft.objects.filter(sku_normalized=item.sku_normalized).delete()
    transaction.on_commit(lambda: square_queue.enqueue(item.sku_normalized))
    return SaveResult(item=item, created=created)


@transaction.atomic
def quick_update(sku: str, field: str, value) -> Item:
    """Change one field from the board / lookup / phone page."""
    if field not in QUICK_EDIT_FIELDS:
        raise ItemError("That field cannot be changed here.")
    item = Item.objects.select_for_update().filter(sku_normalized=normalize_sku(sku)).first()
    if item is None:
        raise ItemError("SKU not found.")
    before = history.snapshot(item)

    if field == "status":
        item.apply_status(str(value or ""))
    elif field == "price":
        item.price = parse_price(value)
    elif field == "reviewed":
        text = str(value).strip().lower()
        item.reviewed = {"2": Review.SOLD, "1": Review.ACTIVE, "true": Review.ACTIVE}.get(text, Review.INACTIVE)
    elif field == "ready":
        item.ready = str(value).strip().lower() in {"1", "true", "yes", "on"}
    elif field == "quantity":
        try:
            item.quantity = max(1, int(str(value).strip() or "1"))
        except ValueError as exc:
            raise ItemError("Quantity must be a whole number.") from exc
    elif field == "notes":
        item.notes = str(value or "")
    item.save()
    history.record(item, ItemEvent.Action.EDITED, before=before)
    transaction.on_commit(lambda: square_queue.enqueue(item.sku_normalized))
    return item


# Fields that can be changed for many items at once from Lookup.
BULK_EDIT_FIELDS = {"status", "ready"}
BULK_LIMIT = 200


def bulk_update(skus, field: str, value) -> tuple[list[Item], list[dict]]:
    """Apply one quick edit to many items. Each item saves on its own, so one bad SKU
    doesn't block the rest. Returns (updated items, [{"sku", "error"}])."""
    if field not in BULK_EDIT_FIELDS:
        raise ItemError("That field can't be changed for several items at once.")
    wanted = list(dict.fromkeys(normalize_sku(s) for s in (skus or []) if normalize_sku(s)))
    if not wanted:
        raise ItemError("Select at least one item.")
    if len(wanted) > BULK_LIMIT:
        raise ItemError(f"Select at most {BULK_LIMIT} items at a time.")
    updated, failed = [], []
    for sku in wanted:
        try:
            updated.append(quick_update(sku, field, value))
        except ItemError as exc:
            failed.append({"sku": sku, "error": str(exc)})
    logger.info("Bulk %s=%s on %d item(s), %d failed", field, value, len(updated), len(failed))
    return updated, failed


@transaction.atomic
def soft_delete(item_id: int) -> Item:
    item = Item.objects.select_for_update().filter(pk=item_id).first()
    if item is None:
        raise ItemError("Record not found.")
    item.deleted_at = timezone.now()
    item.save(update_fields=["deleted_at"], touch=False)
    history.record(item, ItemEvent.Action.DELETED)
    logger.info("Deleted item %s (%s)", item.pk, item.sku_normalized)
    return item


@transaction.atomic
def undo_last_delete() -> Item:
    item = Item.all_objects.select_for_update().filter(deleted_at__isnull=False).order_by("-deleted_at", "-id").first()
    if item is None:
        raise ItemError("Nothing to undo.")
    if Item.objects.filter(sku_normalized=item.sku_normalized).exists():
        raise ItemError(f"SKU {item.sku_normalized} was re-created after it was deleted, so it can't be restored.")
    item.deleted_at = None
    item.save(update_fields=["deleted_at"])
    history.record(item, ItemEvent.Action.RESTORED)
    logger.info("Restored item %s (%s)", item.pk, item.sku_normalized)
    return item


def copy_fields(sku: str) -> dict | None:
    """The newest record for a SKU, minus SKU, ids and timestamps (for "Copy fields")."""
    item = find_item(sku)
    if item is None:
        return None
    data = {}
    for field in COPYABLE_FIELDS:
        value = getattr(item, field)
        if isinstance(value, Decimal):
            value = str(value)
        elif hasattr(value, "isoformat"):
            value = value.isoformat()
        data[field] = value
    return data
