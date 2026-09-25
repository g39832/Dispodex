"""Server-side autosave for the intake sheet.

Every open intake page has a random ``client_id``. Each save carries the
draft version that page last saw; if a *different* page saved a newer version
in the meantime we answer with a conflict instead of silently overwriting
someone else's typing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from core.skus import normalize_sku
from inventory.models import IntakeDraft

MAX_PAYLOAD_BYTES = 64 * 1024

ALLOWED_FIELDS = {
    "sku", "status", "what_is_it", "date_received", "source", "functional", "condition",
    "cords_adapters", "keep_items_together", "picture_taken",
    "power_on", "brand_model", "ram", "ssd_gb", "cpu", "os", "battery_health", "graphics_card",
    "screen_resolution", "diagnostics_test_ran", "wifi_card_installed", "compatible_os",
    "where_it_goes", "price", "quantity", "notes", "ebay_category", "ebay_category_path",
    "ebay_category_id",
}


class DraftError(Exception):
    pass


@dataclass
class DraftSaveResult:
    ok: bool
    version: int
    conflict_payload: dict | None = None


def clean_payload(raw: dict) -> dict:
    clean = {}
    for key, value in raw.items():
        if key not in ALLOWED_FIELDS:
            continue
        if isinstance(value, bool):
            clean[key] = value
        elif isinstance(value, (int, float)):
            clean[key] = value
        elif isinstance(value, str):
            clean[key] = value.strip() if key != "notes" else value
    return clean


def get_draft(sku: str) -> IntakeDraft | None:
    sku_norm = normalize_sku(sku)
    if not sku_norm:
        return None
    return IntakeDraft.objects.filter(sku_normalized=sku_norm).first()


@transaction.atomic
def save_draft(
    sku: str, payload: dict, base_version: int | None, client_id: str, force: bool = False
) -> DraftSaveResult:
    sku_norm = normalize_sku(sku)
    if not sku_norm:
        raise DraftError("SKU is required.")
    if not isinstance(payload, dict):
        raise DraftError("Missing or invalid data payload.")
    data = clean_payload(payload)
    data["sku"] = sku_norm
    if len(json.dumps(data)) > MAX_PAYLOAD_BYTES:
        raise DraftError("Payload too large.")

    draft = IntakeDraft.objects.select_for_update().filter(sku_normalized=sku_norm).first()
    if draft is None:
        draft = IntakeDraft.objects.create(
            sku_normalized=sku_norm, payload=data, version=1, client_id=client_id[:64]
        )
        return DraftSaveResult(ok=True, version=draft.version)

    someone_else_saved = bool(draft.client_id) and draft.client_id != client_id
    if not force and someone_else_saved and (base_version or 0) < draft.version:
        return DraftSaveResult(ok=False, version=draft.version, conflict_payload=draft.payload)

    draft.payload = data
    draft.version += 1
    draft.client_id = client_id[:64]
    draft.updated_at = timezone.now()
    draft.save()
    return DraftSaveResult(ok=True, version=draft.version)


def discard_draft(sku: str) -> None:
    IntakeDraft.objects.filter(sku_normalized=normalize_sku(sku)).delete()
