"""Item history: record who changed what, in words a person can read."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from core import actor as actor_context
from inventory.models import Condition, Functional, Item, ItemEvent, Review, Status

# Tracked fields, in the order they appear on the intake sheet.
TRACKED_FIELDS = {
    "sku": "SKU",
    "status": "Status",
    "what_is_it": "What is it?",
    "price": "Price",
    "quantity": "Quantity",
    "ebay_category": "eBay category",
    "where_it_goes": "Location",
    "date_received": "Date received",
    "source": "Came from",
    "functional": "Functional",
    "condition": "Condition",
    "cords_adapters": "Cords / adapters",
    "keep_items_together": "Keep together",
    "picture_taken": "Picture taken",
    "power_on": "Powers on & stays on",
    "brand_model": "Brand & model",
    "cpu": "CPU",
    "ram": "RAM",
    "ssd_gb": "SSD (GB)",
    "os": "OS",
    "compatible_os": "Compatible OS",
    "battery_health": "Battery health",
    "graphics_card": "Graphics card",
    "screen_resolution": "Screen resolution",
    "diagnostics_test_ran": "Diagnostics test ran",
    "wifi_card_installed": "Wi-Fi card installed",
    "serial_number": "Serial number",
    "fcc_id": "FCC ID",
    "notes": "Notes",
    "reviewed": "Badge",
    "ready": "Ready",
}

# Quick edits from the same person within this window become one history entry,
# so notes typed on the phone page don't produce an entry per pause.
MERGE_WINDOW = timedelta(minutes=10)
MAX_VALUE_LENGTH = 140


def snapshot(item: Item) -> dict:
    return {field: getattr(item, field) for field in TRACKED_FIELDS}


def display(field: str, value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if field == "price":
        return f"${Decimal(value):,.2f}"
    if field == "status":
        return Status(value).label if value in Status.values else str(value)
    if field == "reviewed":
        return Review(value).label if value in Review.values else str(value)
    if field == "date_received":
        return value.strftime("%b %d, %Y") if hasattr(value, "strftime") else str(value)
    if field in ("functional", "condition"):
        choices = Functional if field == "functional" else Condition
        return choices(value).label if value in choices.values else str(value)
    text = " ".join(str(value).split())
    return text if len(text) <= MAX_VALUE_LENGTH else text[: MAX_VALUE_LENGTH - 1] + "…"


def diff(before: dict, after: dict) -> list[dict]:
    changes = []
    for field, label in TRACKED_FIELDS.items():
        old, new = before.get(field), after.get(field)
        if old in (None, "") and new in (None, ""):
            continue
        if old != new:
            changes.append({"field": field, "label": label, "old": display(field, old), "new": display(field, new)})
    return changes


def record(item: Item, action: str, *, before: dict | None = None, note: str = "", actor: str | None = None) -> ItemEvent | None:
    """Add a history entry. For an edit, pass the snapshot taken before the change."""
    who = actor or actor_context.current()
    changes: list[dict] = []
    if action == ItemEvent.Action.EDITED:
        changes = diff(before or {}, snapshot(item))
        if not changes:
            return None
        handled, merged = _merge_into_recent(item, who, changes)
        if handled:
            return merged
    return ItemEvent.objects.create(
        item=item, sku_normalized=item.sku_normalized, action=action,
        changes=changes, note=note[:255], actor=who[:64],
    )


def _merge_into_recent(item: Item, who: str, changes: list[dict]) -> tuple[bool, ItemEvent | None]:
    """Fold a quick follow-up edit into the previous entry. Returns (handled, event)."""
    last = ItemEvent.objects.filter(item=item).order_by("-created_at", "-id").first()
    if (
        last is None
        or last.action != ItemEvent.Action.EDITED
        or last.actor != who
        or timezone.now() - last.created_at > MERGE_WINDOW
    ):
        return False, None
    earlier = {c["field"]: c for c in last.changes}
    if not set(earlier) >= {c["field"] for c in changes}:
        return False, None  # a different kind of edit: keep it as its own entry
    for change in changes:
        earlier[change["field"]]["new"] = change["new"]
    last.changes = [c for c in earlier.values() if c["old"] != c["new"]]
    last.created_at = timezone.now()
    if not last.changes:
        last.delete()  # changed and then changed back
        return True, None
    last.save(update_fields=["changes", "created_at"])
    return True, last


def record_photo(sku_normalized: str, action: str) -> ItemEvent | None:
    """Photo added/removed. A run of uploads by one person becomes one entry ("3 photos added")."""
    item = Item.objects.filter(sku_normalized=sku_normalized).first()
    if item is None:
        return None  # photos taken before the sheet's first save: the "Created" entry covers them
    who = actor_context.current()
    last = ItemEvent.objects.filter(item=item).order_by("-created_at", "-id").first()
    if last and last.action == action and last.actor == who and timezone.now() - last.created_at <= MERGE_WINDOW:
        count = int((last.note.split() or ["1"])[0]) + 1 if last.note[:1].isdigit() else 2
        last.note = f"{count} photos"
        last.created_at = timezone.now()
        last.save(update_fields=["note", "created_at"])
        return last
    return ItemEvent.objects.create(item=item, sku_normalized=sku_normalized, action=action, note="1 photo", actor=who[:64])


def recent_for(item: Item, limit: int = 50) -> list[ItemEvent]:
    return list(ItemEvent.objects.filter(item=item).order_by("-created_at", "-id")[:limit])
