"""Push Dispodex items to the Square catalog (and read inventory counts back).

For each SKU we keep one Square ITEM with one ITEM_VARIATION whose ``sku``
field is the Dispodex SKU. Price, name, description, photo and stock count
all come from the intake record. Unchanged items are skipped using a hash of
everything we send.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.utils import timezone

from core.skus import normalize_sku
from inventory.models import Item, Photo, Status
from inventory.services import photos as photo_service
from inventory.models import ItemEvent
from inventory.services import history
from squaresync.client import SquareClient, SquareError, log_event
from squaresync.config import SquareConfig, get_config
from squaresync.models import CatalogSync, SyncAuditLog

HASH_FIELDS = [
    "sku", "sku_normalized", "status", "what_is_it", "ebay_category", "ebay_category_path", "ebay_category_id",
    "date_received", "source", "functional", "condition", "cords_adapters",
    "keep_items_together", "picture_taken", "power_on", "brand_model", "ram", "ssd_gb", "cpu", "os",
    "battery_health", "graphics_card", "screen_resolution", "where_it_goes", "ebay_status", "price",
    "in_ebay_room", "what_box", "notes", "quantity",
]

DESCRIPTION_LABELS = [
    ("sku", "SKU"),
    ("status", "Dispodex Status"),
    ("what_is_it", "Item"),
    ("ebay_category", "eBay Category"),
    ("ebay_category_path", "eBay Category Path"),
    ("ebay_category_id", "eBay Category ID"),
    ("date_received", "Date Received"),
    ("source", "Source"),
    ("functional", "Functional"),
    ("condition", "Condition"),
    ("cords_adapters", "Cords/Adapters"),
    ("keep_items_together", "Keep Items Together"),
    ("picture_taken", "Picture Taken"),
    ("power_on", "Power On"),
    ("brand_model", "Brand/Model"),
    ("ram", "RAM"),
    ("ssd_gb", "SSD"),
    ("cpu", "CPU"),
    ("os", "OS"),
    ("battery_health", "Battery Health"),
    ("graphics_card", "Graphics Card"),
    ("screen_resolution", "Screen Resolution"),
    ("where_it_goes", "Where It Goes"),
    ("ebay_status", "eBay Status"),
    ("in_ebay_room", "In eBay Room"),
    ("what_box", "Box"),
    ("notes", "Notes"),
]


@dataclass
class SyncResult:
    status: str  # ok | skipped | disabled | error
    message: str

    @property
    def succeeded(self) -> bool:
        return self.status in ("ok", "skipped")

    def as_dict(self) -> dict:
        return {"status": self.status, "message": self.message}


# ── helpers ──────────────────────────────────────────────────────────────────
def correlation_id() -> str:
    return f"sq-{time.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(6)}"


def temp_id(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", value)[:24] or "sku"
    return f"{slug}-{hashlib.sha256(value.encode()).hexdigest()[:10]}"


def limit(value: str, max_len: int) -> str:
    return value if len(value) <= max_len else value[: max(0, max_len - 3)] + "..."


def _field_text(item: Item, field: str) -> str:
    value = getattr(item, field)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value).strip()


def payload_hash(item: Item, photo: Photo | None) -> str:
    payload = {field: _field_text(item, field) for field in HASH_FIELDS}
    payload["photo_id"] = photo.pk if photo else None
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def item_name(item: Item) -> str:
    parts = []
    for field in ("brand_model", "what_is_it"):
        value = (getattr(item, field) or "").strip()
        if value and value not in parts:
            parts.append(value)
    return limit(" - ".join(parts) if parts else item.sku_normalized or "Dispodex Item", 512)


def item_description(item: Item) -> str:
    lines = []
    for field, label in DESCRIPTION_LABELS:
        value = _field_text(item, field)
        if value:
            lines.append(f"{label}: {value}")
    return limit("\n".join(lines), 4000)


def build_catalog_object(item: Item, config: SquareConfig, existing: dict | None) -> dict:
    sku = item.sku_normalized
    existing_item = (existing or {}).get("item") or {}
    existing_variation = (existing or {}).get("variation") or {}
    item_id = existing_item.get("id") or f"#pink-item-{temp_id(sku)}"
    variation_id = existing_variation.get("id") or f"#pink-var-{temp_id(sku)}"

    variation_data: dict = {"item_id": item_id, "name": "Default", "sku": sku, "track_inventory": True}
    if item.price is not None:
        variation_data["pricing_type"] = "FIXED_PRICING"
        cents = (Decimal(str(item.price)) * 100).to_integral_value()
        variation_data["price_money"] = {"amount": int(cents), "currency": config.currency}
    else:
        variation_data["pricing_type"] = "VARIABLE_PRICING"
    variation = {
        "type": "ITEM_VARIATION",
        "id": variation_id,
        "present_at_all_locations": True,
        "item_variation_data": variation_data,
    }
    if "version" in existing_variation:
        variation["version"] = int(existing_variation["version"])

    variations = [variation]
    existing_variations = (existing_item.get("item_data") or {}).get("variations") or []
    if existing_variations:
        variations, replaced = [], False
        for other in existing_variations:
            if not isinstance(other, dict):
                continue
            other_sku = normalize_sku((other.get("item_variation_data") or {}).get("sku"))
            if other.get("id") == variation_id or other_sku == sku:
                variations.append(variation)
                replaced = True
            else:
                variations.append({k: v for k, v in other.items() if k not in ("created_at", "updated_at")})
        if not replaced:
            variations.append(variation)

    item_data = {
        "name": item_name(item),
        "description": item_description(item),
        "product_type": "REGULAR",
        "variations": variations,
    }
    image_ids = (existing_item.get("item_data") or {}).get("image_ids")
    if image_ids:
        item_data["image_ids"] = list(image_ids)
    obj = {"type": "ITEM", "id": item_id, "present_at_all_locations": True, "item_data": item_data}
    if "version" in existing_item:
        obj["version"] = int(existing_item["version"])
    return obj


def _extract_item_and_variation(obj, related: list, sku: str) -> dict | None:
    if not isinstance(obj, dict) or obj.get("type") != "ITEM":
        return None
    variations = list((obj.get("item_data") or {}).get("variations") or [])
    variations += [r for r in related or [] if isinstance(r, dict) and r.get("type") == "ITEM_VARIATION"]
    for variation in variations:
        if isinstance(variation, dict) and normalize_sku((variation.get("item_variation_data") or {}).get("sku")) == sku:
            return {"item": obj, "variation": variation}
    return None


def find_existing(client: SquareClient, mapping: CatalogSync | None, sku: str) -> dict | None:
    """Find the Square item for a SKU: by the saved ID first, then by searching."""
    if mapping and mapping.square_item_id:
        try:
            resp = client.get(f"/v2/catalog/object/{mapping.square_item_id}?include_related_objects=true")
            found = _extract_item_and_variation(resp.get("object"), resp.get("related_objects") or [], sku)
            if found:
                return found
        except SquareError as exc:
            log_event(operation="catalog_retrieve", sku=sku, status="failure", message=str(exc))
    try:
        resp = client.post(
            "/v2/catalog/search-catalog-items", {"text_filter": sku, "product_types": ["REGULAR"], "limit": 10}
        )
        for obj in resp.get("items") or []:
            found = _extract_item_and_variation(obj, [], sku)
            if found:
                return found
    except SquareError as exc:
        log_event(operation="catalog_search", sku=sku, status="failure", message=str(exc))
    return None


def _first_of_type(catalog_object, id_mappings, object_type: str) -> dict | None:
    if isinstance(catalog_object, dict):
        if catalog_object.get("type") == object_type:
            return catalog_object
        if object_type == "ITEM_VARIATION":
            for variation in (catalog_object.get("item_data") or {}).get("variations") or []:
                if isinstance(variation, dict) and variation.get("type") == "ITEM_VARIATION":
                    return variation
    for mapping in id_mappings or []:
        if mapping.get("object_type") == object_type and mapping.get("object_id"):
            return {"id": mapping["object_id"]}
    return None


def upload_image(client: SquareClient, item_id: str, sku: str, photo: Photo) -> dict | None:
    if photo.mime_type not in ("image/jpeg", "image/png", "image/gif"):
        log_event(operation="image_upload", sku=sku, status="skipped", message=f"unsupported type {photo.mime_type}")
        return None
    path = photo_service.photo_path(photo)
    if not path.exists():
        return None
    request_body = {
        "idempotency_key": "pink-img-" + hashlib.sha256(f"{sku}:{photo.pk}:{path.stat().st_size}".encode()).hexdigest()[:28],
        "object_id": item_id,
        "is_primary": True,
        "image": {
            "type": "IMAGE",
            "id": f"#pink-image-{temp_id(f'{sku}-{photo.pk}')}",
            "image_data": {"name": limit(photo.original_name or sku, 255), "caption": f"Dispodex photo for SKU {sku}"},
        },
    }
    with path.open("rb") as handle:
        resp = client.request(
            "POST",
            "/v2/catalog/images",
            files={
                "request": (None, json.dumps(request_body), "application/json"),
                "image_file": (photo.original_name or "photo", handle, photo.mime_type),
            },
        )
    image = resp.get("image")
    return image if isinstance(image, dict) else None


def target_quantity(item: Item, config: SquareConfig) -> int:
    if item.status == Status.SOLD:
        return 0
    return item.quantity if item.quantity > 0 else config.default_quantity


def set_inventory_count(client: SquareClient, config: SquareConfig, variation_id: str, item: Item, hash_value: str) -> int:
    quantity = target_quantity(item, config)
    key = hashlib.sha256(f"{item.sku_normalized}:{variation_id}:{quantity}:{hash_value}".encode()).hexdigest()[:28]
    client.post(
        "/v2/inventory/batch-change",
        {
            "idempotency_key": f"pink-inv-{key}",
            "changes": [
                {
                    "type": "PHYSICAL_COUNT",
                    "physical_count": {
                        "catalog_object_id": variation_id,
                        "location_id": config.location_id,
                        "quantity": str(quantity),
                        "state": "IN_STOCK",
                        "occurred_at": datetime.now(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                }
            ],
        },
    )
    return quantity


def audit(operation: str, sku: str, direction: str, status: str, started: float, **extra) -> None:
    normalized = {"ok": "success", "success": "success", "error": "failure", "failure": "failure"}.get(status, "skipped")
    SyncAuditLog.objects.create(
        operation=operation,
        sku_normalized=sku,
        direction=direction,
        status=normalized,
        duration_ms=int((time.monotonic() - started) * 1000),
        **{k: (v if v is not None else "") for k, v in extra.items()},
    )


# ── main entry points ────────────────────────────────────────────────────────
def sync_item(sku: str, client: SquareClient | None = None, config: SquareConfig | None = None) -> SyncResult:
    """Create or update the Square catalog item, photo and stock count for one SKU."""
    started = time.monotonic()
    sku = normalize_sku(sku)
    cid = correlation_id()
    config = config or get_config()
    if not sku:
        return SyncResult("skipped", "SKU is empty")
    if not config.enabled:
        return SyncResult("disabled", "Square sync is not configured")
    item = Item.objects.filter(sku_normalized=sku).first()
    if item is None:
        log_event(operation="catalog_sync", sku=sku, correlation_id=cid, status="skipped", message="SKU not found")
        return SyncResult("skipped", "SKU not found")

    client = client or SquareClient(config)
    mapping = CatalogSync.objects.filter(sku_normalized=sku).first()
    photo = photo_service.preferred_photo(sku)
    hash_value = payload_hash(item, photo)
    if mapping and mapping.payload_hash == hash_value and mapping.is_mapped and not mapping.last_error:
        log_event(operation="catalog_sync", sku=sku, correlation_id=cid, status="skipped",
                  message="Square already has the latest payload")
        return SyncResult("skipped", "Square already has the latest details")

    try:
        existing = find_existing(client, mapping, sku)
        catalog_object = build_catalog_object(item, config, existing)
        # The key changes whenever the body changes, so a retry of the exact same
        # body is idempotent and Square never reports IDEMPOTENCY_KEY_REUSED.
        idempotency_key = "pink-" + hashlib.sha256(
            f"{sku}:{json.dumps(catalog_object, sort_keys=True)}".encode()
        ).hexdigest()[:32]
        result = client.post("/v2/catalog/object", {"idempotency_key": idempotency_key, "object": catalog_object})
        square_item = _first_of_type(result.get("catalog_object"), result.get("id_mappings"), "ITEM") or {}
        square_variation = _first_of_type(result.get("catalog_object"), result.get("id_mappings"), "ITEM_VARIATION") or {}
        item_id = square_item.get("id") or ((existing or {}).get("item") or {}).get("id") or ""
        variation_id = square_variation.get("id") or ((existing or {}).get("variation") or {}).get("id") or ""
        if not item_id or not variation_id:
            raise SquareError("Square did not return catalog item and variation IDs.")

        image_id = mapping.square_image_id if mapping else ""
        image_photo_id = mapping.square_image_photo_id if mapping else None
        if photo and photo.pk != image_photo_id:
            image = upload_image(client, item_id, sku, photo)
            if image:
                image_id, image_photo_id = image.get("id") or image_id, photo.pk

        quantity = set_inventory_count(client, config, variation_id, item, hash_value)
        CatalogSync.objects.update_or_create(
            sku_normalized=sku,
            defaults={
                "square_item_id": item_id,
                "square_item_version": square_item.get("version") or ((existing or {}).get("item") or {}).get("version"),
                "square_variation_id": variation_id,
                "square_variation_version": square_variation.get("version")
                or ((existing or {}).get("variation") or {}).get("version"),
                "square_image_id": image_id or "",
                "square_image_photo_id": image_photo_id,
                "payload_hash": hash_value,
                "last_synced_at": timezone.now(),
                "last_error": "",
                "last_origin": "local",
                "last_correlation_id": cid,
                "last_local_change_at": item.updated_at,
                "last_synced_quantity": quantity,
                "last_synced_price": item.price,
                "updated_at": timezone.now(),
            },
        )
        log_event(operation="catalog_sync", sku=sku, correlation_id=cid, object_type="ITEM_VARIATION",
                  object_id=variation_id, status="success", message="Square catalog synced")
        audit("catalog_sync", sku, "push", "success", started, object_type="ITEM_VARIATION",
              object_id=variation_id, correlation_id=cid, response_summary="Square catalog synced")
        return SyncResult("ok", "Square catalog synced")
    except SquareError as exc:
        CatalogSync.objects.update_or_create(
            sku_normalized=sku, defaults={"last_error": limit(str(exc), 1000), "updated_at": timezone.now()}
        )
        log_event(operation="catalog_sync", sku=sku, correlation_id=cid, status="failure", message=str(exc))
        audit("catalog_sync", sku, "push", "failure", started, error_message=str(exc)[:2000], correlation_id=cid)
        return SyncResult("error", str(exc))


def push_inventory(sku: str, client: SquareClient | None = None, config: SquareConfig | None = None) -> SyncResult:
    """Only update the stock count (e.g. after a sale arrived from Square)."""
    config = config or get_config()
    if not config.enabled:
        return SyncResult("disabled", "Square sync is not configured")
    sku = normalize_sku(sku)
    mapping = CatalogSync.objects.filter(sku_normalized=sku).first()
    item = Item.objects.filter(sku_normalized=sku).first()
    if not mapping or not mapping.square_variation_id:
        # Never pushed before — do a full sync instead.
        return sync_item(sku, client=client, config=config)
    if item is None:
        return SyncResult("skipped", "SKU not found")
    client = client or SquareClient(config)
    try:
        quantity = set_inventory_count(client, config, mapping.square_variation_id, item, mapping.payload_hash)
    except SquareError as exc:
        return SyncResult("error", str(exc))
    mapping.last_inventory_sync_at = timezone.now()
    mapping.last_synced_quantity = quantity
    mapping.save(update_fields=["last_inventory_sync_at", "last_synced_quantity"])
    return SyncResult("ok", f"Inventory set for {sku}")


def pull_inventory(sku: str, client: SquareClient | None = None, config: SquareConfig | None = None) -> SyncResult:
    """If Square says the SKU has no stock left, mark it SOLD here."""
    config = config or get_config()
    if not config.enabled:
        return SyncResult("disabled", "Square sync is not configured")
    sku = normalize_sku(sku)
    client = client or SquareClient(config)
    mapping = CatalogSync.objects.filter(sku_normalized=sku).first()
    variation_id = mapping.square_variation_id if mapping else ""
    if not variation_id:
        existing = find_existing(client, mapping, sku)
        variation_id = ((existing or {}).get("variation") or {}).get("id", "")
        if not variation_id:
            return SyncResult("skipped", f"No Square mapping found for {sku}")
    try:
        resp = client.get(f"/v2/inventory/{variation_id}?location_ids={config.location_id}")
    except SquareError as exc:
        return SyncResult("error", str(exc))
    for count in resp.get("counts") or []:
        if count.get("catalog_object_id") == variation_id and count.get("quantity") is not None:
            if int(Decimal(str(count["quantity"]))) <= 0:
                item = Item.objects.filter(sku_normalized=sku).exclude(status=Status.SOLD).first()
                if item:
                    before = history.snapshot(item)
                    item.apply_status(Status.SOLD)
                    item.save()
                    history.record(item, ItemEvent.Action.EDITED, before=before, actor="Square", note="Out of stock in Square")
                    log_event(operation="inventory_pull", sku=sku, status="updated", message="marked as sold")
            break
    return SyncResult("ok", "Inventory pulled from Square")


def test_connection(config: SquareConfig | None = None) -> dict:
    """Check the token and location by asking Square for the location."""
    config = config or get_config()
    missing = config.missing()
    if missing:
        return {"ok": False, "message": "Missing in .env: " + ", ".join(missing)}
    client = SquareClient(config)
    try:
        resp = client.get(f"/v2/locations/{config.location_id}")
    except SquareError as exc:
        return {"ok": False, "message": str(exc)}
    location = resp.get("location") or {}
    return {
        "ok": True,
        "message": f"Connected to Square ({config.environment}) — location “{location.get('name', config.location_id)}”.",
        "location_name": location.get("name", ""),
        "currency": location.get("currency", ""),
    }
