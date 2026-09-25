"""Incoming Square webhooks: sales, refunds, inventory and catalog changes.

Square signs each call with HMAC-SHA256 over ``notification_url + raw body``
using the subscription's signature key. We verify that, reject stale events,
ignore duplicates, and then:

* completed payment / order → record the sale, mark the SKU SOLD
* completed refund          → move the SKU back to Intake with a note
* inventory count change    → keep stock/status in step (newest change wins)
* catalog change            → queue a re-sync of that SKU

A webhook never triggers an outbound catalog push directly, which prevents
feedback loops.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.skus import normalize_sku
from inventory.models import Item, Status
from inventory.models import ItemEvent
from inventory.services import history
from squaresync import queue as square_queue
from squaresync.client import SquareClient, SquareError, log_event
from squaresync.config import SquareConfig, get_config
from squaresync.models import CatalogSync, Sale, SyncAuditLog, SyncJob, WebhookEvent

REFUND_NOTE = "Returned via Square refund — needs inspection."


def verify_signature(raw_body: bytes, signature: str, key: str, notification_url: str) -> bool:
    if not signature or not key or not notification_url:
        return False
    digest = hmac.new(key.encode(), notification_url.encode() + raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature.strip())


def is_fresh(body: dict, max_age_seconds: int) -> bool:
    created = str(body.get("created_at") or "")
    if not created:
        return True
    moment = parse_datetime(created)
    if moment is None:
        return False
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment, dt_timezone.utc)
    return abs((timezone.now() - moment).total_seconds()) <= max_age_seconds


def _money(value) -> Decimal:
    try:
        return (Decimal(int((value or {}).get("amount") or 0)) / 100).quantize(Decimal("0.01"))
    except (TypeError, ValueError, AttributeError):
        return Decimal("0.00")


def _parse_time(value: str) -> datetime:
    moment = parse_datetime(value or "") if value else None
    if moment is None:
        return timezone.now()
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment, dt_timezone.utc)
    return moment


def resolve_sku(catalog_object_id: str, variation_name: str = "") -> str | None:
    if catalog_object_id:
        mapping = (
            CatalogSync.objects.filter(square_variation_id=catalog_object_id).first()
            or CatalogSync.objects.filter(square_item_id=catalog_object_id).first()
        )
        if mapping:
            return mapping.sku_normalized
    if variation_name:
        candidate = normalize_sku(variation_name)
        if Item.objects.filter(sku_normalized=candidate).exists():
            return candidate
    return None


def _mark_sold(sku: str) -> None:
    item = Item.objects.filter(sku_normalized=sku).first()
    if item and item.status != Status.SOLD:
        mapping = CatalogSync.objects.filter(sku_normalized=sku).first()
        if mapping:
            mapping.previous_status = item.status
            mapping.save(update_fields=["previous_status"])
        before = history.snapshot(item)
        item.apply_status(Status.SOLD)
        item.save()
        history.record(item, ItemEvent.Action.EDITED, before=before, actor="Square", note="Sold in Square")


def process_line_item(line: dict, order_id: str, payment_id: str, sold_at: datetime, location_id: str, cid: str) -> dict:
    sku = resolve_sku(str(line.get("catalog_object_id") or ""), str(line.get("variation_name") or ""))
    if sku is None:
        return {"status": "skipped", "message": f"Could not resolve SKU for {line.get('catalog_object_id') or line.get('name')}"}
    if Sale.objects.filter(square_order_id=order_id, sku_normalized=sku).exists():
        return {"sku": sku, "status": "duplicate", "message": "Sale already recorded"}
    tax = sum((_money(t.get("applied_money")) for t in line.get("applied_taxes") or []), Decimal("0"))
    discount = sum((_money(d.get("applied_money")) for d in line.get("applied_discounts") or []), Decimal("0"))
    try:
        quantity = max(1, int(Decimal(str(line.get("quantity") or "1"))))
    except (ValueError, ArithmeticError):
        quantity = 1
    Sale.objects.create(
        sku=sku,
        sku_normalized=sku,
        square_order_id=order_id,
        square_payment_id=payment_id,
        sale_price=_money(line.get("total_money")),
        tax_amount=tax,
        discount_amount=discount,
        line_item_quantity=quantity,
        sold_at=sold_at,
        location_id=location_id,
        receipt_number=str(line.get("uid") or "")[:64],
    )
    _mark_sold(sku)
    CatalogSync.objects.filter(sku_normalized=sku).update(
        last_sale_sync_at=timezone.now(), last_origin="square", last_correlation_id=cid
    )
    transaction.on_commit(lambda: square_queue.enqueue(sku, SyncJob.Operation.INVENTORY_SET))
    return {"sku": sku, "status": "sold", "message": f"Sale recorded for {sku}"}


def process_order(order: dict, payment: dict | None, cid: str) -> dict:
    order_id = str(order.get("id") or "")
    state = str(order.get("state") or "").upper()
    if state not in ("COMPLETED", "DONE"):
        return {"status": "skipped", "message": f"Order state is {state or 'unknown'} (not completed)"}
    lines = order.get("line_items") or []
    if not lines:
        return {"status": "skipped", "message": f"No line items in order {order_id}"}
    sold_at = _parse_time(str(order.get("created_at") or ""))
    payment_id = str((payment or {}).get("id") or "")
    results = [
        process_line_item(line, order_id, payment_id, sold_at, str(order.get("location_id") or ""), cid)
        for line in lines
        if isinstance(line, dict)
    ]
    sold = sum(1 for r in results if r.get("status") == "sold")
    return {"status": "ok" if sold else "skipped", "message": f"{sold} sold, {len(results) - sold} skipped from order {order_id}"}


def process_payment(body: dict, client: SquareClient | None, cid: str) -> dict:
    payment = ((body.get("data") or {}).get("object") or {}).get("payment")
    if not isinstance(payment, dict) or str(payment.get("status") or "").upper() != "COMPLETED":
        return {"status": "skipped", "message": "Payment is not completed"}
    payment_id, order_id = str(payment.get("id") or ""), str(payment.get("order_id") or "")
    if not payment_id or not order_id:
        return {"status": "error", "message": "Completed payment is missing id or order_id"}
    client = client or SquareClient(get_config())
    order = client.get(f"/v2/orders/{order_id}").get("order")
    if not isinstance(order, dict):
        return {"status": "error", "message": "Square order missing"}
    return process_order(order, payment, cid)


def process_refund(body: dict) -> dict:
    refund = ((body.get("data") or {}).get("object") or {}).get("refund")
    if not isinstance(refund, dict) or str(refund.get("status") or "").upper() != "COMPLETED":
        return {"status": "skipped", "message": "Refund is not completed"}
    payment_id, order_id = str(refund.get("payment_id") or ""), str(refund.get("order_id") or "")
    if not payment_id and not order_id:
        return {"status": "error", "message": "Completed refund is missing payment_id or order_id"}
    sales = Sale.objects.filter(square_payment_id=payment_id) if payment_id else Sale.objects.filter(square_order_id=order_id)
    updated = 0
    for sku in set(sales.values_list("sku_normalized", flat=True)):
        item = Item.objects.filter(sku_normalized=sku, status=Status.SOLD).first()
        if item:
            before = history.snapshot(item)
            item.apply_status(Status.INTAKE)
            item.notes = (item.notes.rstrip() + "\n" + REFUND_NOTE).strip()
            item.save()
            history.record(item, ItemEvent.Action.EDITED, before=before, actor="Square", note="Refunded in Square")
            updated += 1
    return {"status": "ok", "message": f"{updated} refunded item(s) moved back to Intake"}


def process_inventory(body: dict, config: SquareConfig, cid: str) -> dict:
    obj = (body.get("data") or {}).get("object") or {}
    counts = obj.get("inventory_counts") or [obj.get("inventory_count") or obj]
    messages = []
    for count in counts:
        if not isinstance(count, dict):
            continue
        messages.append(_process_inventory_count(count, body, config, cid))
    return {"status": "ok", "message": "; ".join(messages) or "No inventory counts in event"}


def _process_inventory_count(count: dict, body: dict, config: SquareConfig, cid: str) -> str:
    variation_id = str(count.get("catalog_object_id") or "")
    location_id = str(count.get("location_id") or "")
    if not variation_id:
        return "No catalog_object_id in inventory event"
    if str(count.get("state") or "IN_STOCK").upper() != "IN_STOCK":
        return f"Ignored {count.get('state')} count for {variation_id}"
    if config.location_id and location_id and location_id != config.location_id:
        return "Inventory event for a different location"
    sku = resolve_sku(variation_id)
    if sku is None:
        return f"No SKU mapping for {variation_id}"
    item = Item.objects.filter(sku_normalized=sku).first()
    if item is None:
        return f"SKU {sku} not found"
    mapping = CatalogSync.objects.filter(sku_normalized=sku).first()
    occurred = _parse_time(str(count.get("calculated_at") or count.get("occurred_at") or body.get("created_at") or ""))
    local_change = (mapping.last_local_change_at if mapping and mapping.last_local_change_at else None) or item.updated_at
    if occurred < local_change:
        return "Square inventory event is older than the latest Dispodex change"
    try:
        quantity = int(Decimal(str(count.get("quantity") or "0")))
    except (ValueError, ArithmeticError):
        quantity = 0

    new_status = item.status
    if quantity <= 0:
        if item.status != Status.SOLD and mapping:
            mapping.previous_status = item.status
        new_status = Status.SOLD
    elif item.status == Status.SOLD:
        new_status = (mapping.previous_status if mapping else "") or ""
        if not new_status:
            return "Positive Square stock but no previous Dispodex status to restore"
    changed = new_status != item.status or (quantity > 0 and quantity != item.quantity)
    if changed:
        before = history.snapshot(item)
        item.apply_status(new_status)
        if quantity > 0:
            item.quantity = quantity
        item.save()
        history.record(item, ItemEvent.Action.EDITED, before=before, actor="Square", note="Stock changed in Square")
    if mapping:
        mapping.last_inventory_sync_at = timezone.now()
        mapping.last_square_change_at = occurred
        mapping.last_origin = "square"
        mapping.last_correlation_id = cid
        mapping.last_synced_quantity = max(0, quantity)
        mapping.save()
    return f"Inventory processed for {sku} (qty={quantity})"


def process_catalog(body: dict, cid: str) -> dict:
    obj = (body.get("data") or {}).get("object") or {}
    catalog_object = obj.get("catalog_object") or obj
    sku = ""
    if catalog_object.get("type") == "ITEM":
        for variation in (catalog_object.get("item_data") or {}).get("variations") or []:
            sku = normalize_sku((variation.get("item_variation_data") or {}).get("sku"))
            if sku:
                break
    elif catalog_object.get("type") == "ITEM_VARIATION":
        sku = normalize_sku((catalog_object.get("item_variation_data") or {}).get("sku"))
    if sku and Item.objects.filter(sku_normalized=sku).exists():
        transaction.on_commit(lambda: square_queue.enqueue(sku, priority=30))
        return {"status": "ok", "message": f"Catalog change queued a re-sync of {sku}"}
    return {"status": "skipped", "message": "Catalog event has no Dispodex SKU"}


def handle_event(body: dict, client: SquareClient | None = None, config: SquareConfig | None = None) -> dict:
    """Apply one verified webhook event. Safe to call twice with the same event."""
    config = config or get_config()
    started = time.monotonic()
    event_id = str(body.get("event_id") or "")
    event_type = str(body.get("type") or "")
    if not event_id:
        return {"status": "skipped", "message": "No event_id in payload"}
    body_hash = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    cid = f"wh-{event_id}"[:64]

    if WebhookEvent.objects.filter(event_id=event_id).exists():
        SyncAuditLog.objects.create(operation=event_type[:64], direction="pull", status="skipped", webhook_id=event_id,
                                    correlation_id=cid, request_body_hash=body_hash,
                                    response_summary=f"Duplicate event {event_id}")
        return {"status": "duplicate", "message": "Event already processed"}

    with transaction.atomic():
        try:
            if event_type.startswith("payment."):
                result = process_payment(body, client, cid)
            elif event_type.startswith("refund."):
                result = process_refund(body)
            elif event_type.startswith("order."):
                data_obj = (body.get("data") or {}).get("object") or {}
                order = data_obj.get("order") or data_obj.get("order_updated") or {}
                result = process_order(order, data_obj.get("payment"), cid) if order.get("line_items") else {
                    "status": "skipped", "message": "Order event without line items (payment event will follow)"}
            elif event_type.startswith("inventory."):
                result = process_inventory(body, config, cid)
            elif event_type.startswith("catalog."):
                result = process_catalog(body, cid)
            else:
                result = {"status": "ignored", "message": f"Unhandled event type: {event_type}"}
        except SquareError as exc:
            result = {"status": "error", "message": str(exc)}
        if result["status"] == "error":
            transaction.set_rollback(True)
    if result["status"] != "error":
        # Recorded only when handled, so Square's automatic retry gets another chance on errors.
        WebhookEvent.objects.get_or_create(
            event_id=event_id,
            defaults={
                "event_type": event_type[:64],
                "merchant_id": str(body.get("merchant_id") or "")[:64],
                "location_id": str(body.get("location_id") or "")[:64],
                "body_hash": body_hash,
                "result_status": result["status"][:16],
                "result_message": result.get("message", ""),
            },
        )
    SyncAuditLog.objects.create(
        operation=event_type[:64],
        direction="pull",
        status={"ok": "success", "error": "failure"}.get(result["status"], "skipped"),
        duration_ms=int((time.monotonic() - started) * 1000),
        webhook_id=event_id,
        correlation_id=cid,
        request_body_hash=body_hash,
        response_summary=result.get("message", "")[:2000],
        error_message=result.get("message", "")[:2000] if result["status"] == "error" else "",
    )
    log_event(operation=event_type, direction="pull", webhook_id=event_id, correlation_id=cid,
              status=result["status"], message=result.get("message"))
    return result
