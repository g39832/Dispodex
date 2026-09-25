import base64
import hashlib
import hmac
import json
from datetime import timedelta

import pytest
import responses
from django.urls import reverse
from django.utils import timezone

from inventory.models import Item, Review, Status
from squaresync import processor, reconciliation
from squaresync import queue as square_queue
from squaresync import sync as square_sync
from squaresync.config import get_config
from squaresync.models import CatalogSync, Sale, SyncJob, WebhookEvent

pytestmark = pytest.mark.django_db(transaction=True)
BASE = "https://connect.squareupsandbox.com"


def catalog_upsert_response(item_id="ITEM1", var_id="VAR1"):
    return {
        "catalog_object": {
            "type": "ITEM", "id": item_id, "version": 5,
            "item_data": {"variations": [{"type": "ITEM_VARIATION", "id": var_id, "version": 6}]},
        }
    }


def mock_happy_sync():
    responses.post(f"{BASE}/v2/catalog/search-catalog-items", json={"items": []})
    responses.post(f"{BASE}/v2/catalog/object", json=catalog_upsert_response())
    responses.post(f"{BASE}/v2/inventory/batch-change", json={"counts": []})


# ── config ───────────────────────────────────────────────────────────────────
def test_square_off_without_credentials():
    assert get_config().enabled is False
    assert square_sync.sync_item("X").status == "disabled"


def test_placeholder_credentials_do_not_count(settings):
    settings.SQUARE = {**settings.SQUARE, "ACCESS_TOKEN": "replace-with-token", "LOCATION_ID": "L"}
    assert get_config().enabled is False


def test_jobs_wait_until_square_is_configured(item):
    square_queue.enqueue(item.sku)
    summary = processor.process_queue()
    assert summary["waiting_for_config"] is True
    assert SyncJob.objects.get().status == SyncJob.State.QUEUED


# ── catalog object ───────────────────────────────────────────────────────────
def test_catalog_object_for_new_item(square_settings, item):
    obj = square_sync.build_catalog_object(item, get_config(), None)
    assert obj["type"] == "ITEM" and obj["id"].startswith("#pink-item-")
    variation = obj["item_data"]["variations"][0]
    assert variation["item_variation_data"]["sku"] == "ABC-1"
    assert variation["item_variation_data"]["price_money"] == {"amount": 12000, "currency": "USD"}
    assert obj["item_data"]["name"] == "Dell 5590 - Laptop"
    assert "SKU: ABC-1" in obj["item_data"]["description"]


def test_catalog_object_without_price_uses_variable_pricing(square_settings):
    item = Item.objects.create(sku="NP-1", what_is_it="Thing")
    obj = square_sync.build_catalog_object(item, get_config(), None)
    assert obj["item_data"]["variations"][0]["item_variation_data"]["pricing_type"] == "VARIABLE_PRICING"


# ── sync ─────────────────────────────────────────────────────────────────────
@responses.activate
def test_sync_item_creates_mapping_and_sets_stock(square_settings, item):
    mock_happy_sync()
    result = square_sync.sync_item(item.sku)
    assert result.status == "ok", result.message
    mapping = CatalogSync.objects.get(sku_normalized="ABC-1")
    assert (mapping.square_item_id, mapping.square_variation_id) == ("ITEM1", "VAR1")
    inventory_call = json.loads(responses.calls[-1].request.body)
    assert inventory_call["changes"][0]["physical_count"]["quantity"] == "1"
    assert responses.calls[-1].request.headers["Authorization"] == "Bearer test-token"


@responses.activate
def test_unchanged_item_is_skipped(square_settings, item):
    mock_happy_sync()
    square_sync.sync_item(item.sku)
    calls = len(responses.calls)
    assert square_sync.sync_item(item.sku).status == "skipped"
    assert len(responses.calls) == calls


@responses.activate
def test_sold_items_get_zero_stock(square_settings, item):
    mock_happy_sync()
    item.apply_status(Status.SOLD)
    item.save()
    square_sync.sync_item(item.sku)
    body = json.loads(responses.calls[-1].request.body)
    assert body["changes"][0]["physical_count"]["quantity"] == "0"


@responses.activate
def test_sync_error_is_recorded(square_settings, item):
    responses.post(f"{BASE}/v2/catalog/search-catalog-items", json={"items": []})
    responses.post(f"{BASE}/v2/catalog/object", status=401,
                   json={"errors": [{"category": "AUTHENTICATION_ERROR", "code": "UNAUTHORIZED", "detail": "bad token"}]})
    result = square_sync.sync_item(item.sku)
    assert result.status == "error"
    assert "bad token" in CatalogSync.objects.get().last_error


@responses.activate
def test_transient_errors_are_retried(square_settings, item):
    responses.post(f"{BASE}/v2/catalog/search-catalog-items", json={"items": []})
    responses.post(f"{BASE}/v2/catalog/object", status=503, json={"errors": []})
    responses.post(f"{BASE}/v2/catalog/object", json=catalog_upsert_response())
    responses.post(f"{BASE}/v2/inventory/batch-change", json={})
    client = square_sync.SquareClient(get_config(), sleep=lambda s: None)
    assert square_sync.sync_item(item.sku, client=client).status == "ok"


@responses.activate
def test_queue_processes_and_retries(square_settings, item):
    responses.post(f"{BASE}/v2/catalog/search-catalog-items", json={"items": []})
    responses.post(f"{BASE}/v2/catalog/object", status=400, json={"errors": [{"detail": "nope"}]})
    square_queue.enqueue(item.sku)
    summary = processor.process_queue()
    assert summary["failed"] == 1
    job = SyncJob.objects.get()
    assert job.status == SyncJob.State.RETRYING and job.retry_count == 1 and job.next_retry_at > timezone.now()
    # Not due yet → nothing runs
    assert processor.process_queue()["processed"] == 0


def test_requeue_after_completion_runs_again(item):
    job = square_queue.enqueue(item.sku)
    job.status = SyncJob.State.COMPLETED
    job.save()
    square_queue.enqueue(item.sku)
    assert SyncJob.objects.get().status == SyncJob.State.QUEUED


def test_change_while_processing_is_not_lost(item):
    square_queue.enqueue(item.sku)
    [job] = square_queue.claim_due_jobs()
    square_queue.enqueue(item.sku)  # edited again mid-sync
    square_queue.mark_completed(job)
    assert SyncJob.objects.get().status == SyncJob.State.QUEUED


def test_dead_letter_after_max_retries(item):
    job = square_queue.enqueue(item.sku)
    job.max_retries = 2
    job.save()
    square_queue.mark_failed(job, "x")
    square_queue.mark_failed(job, "x")
    assert SyncJob.objects.get().status == SyncJob.State.DEAD_LETTER
    assert square_queue.reset_dead_letters() == 1


# ── webhooks ─────────────────────────────────────────────────────────────────
def signed_post(client, body, key="sig-key", url="https://shop.example.com/webhooks/square/"):
    raw = json.dumps(body).encode()
    signature = base64.b64encode(hmac.new(key.encode(), url.encode() + raw, hashlib.sha256).digest()).decode()
    return client.post(reverse("square_webhook"), raw, content_type="application/json",
                       HTTP_X_SQUARE_HMACSHA256_SIGNATURE=signature)


def order_event(event_id="evt-1", sku="ABC-1", state="COMPLETED"):
    return {
        "event_id": event_id, "type": "order.updated", "merchant_id": "M1",
        "created_at": timezone.now().isoformat(),
        "data": {"object": {"order": {
            "id": "ORDER1", "state": state, "location_id": "LOC1", "created_at": timezone.now().isoformat(),
            "line_items": [{"uid": "L1", "variation_name": sku, "quantity": "1", "total_money": {"amount": 11500},
                            "applied_taxes": [{"applied_money": {"amount": 900}}]}],
        }}},
    }


def test_webhook_rejects_bad_signature(client, square_settings):
    response = client.post(reverse("square_webhook"), b"{}", content_type="application/json",
                           HTTP_X_SQUARE_HMACSHA256_SIGNATURE="wrong")
    assert response.status_code == 401


def test_webhook_disabled_without_key(client):
    assert client.post(reverse("square_webhook"), b"{}", content_type="application/json").status_code == 503


def test_webhook_sale_marks_item_sold_once(client, square_settings, item):
    response = signed_post(client, order_event())
    assert response.status_code == 200, response.content
    item.refresh_from_db()
    assert item.status == Status.SOLD and item.reviewed == Review.SOLD
    sale = Sale.objects.get()
    assert str(sale.sale_price) == "115.00" and str(sale.tax_amount) == "9.00"
    assert SyncJob.objects.filter(operation="inventory_set").exists()
    # Square re-sends the same event: nothing changes
    assert signed_post(client, order_event()).json()["status"] == "duplicate"
    assert Sale.objects.count() == 1 and WebhookEvent.objects.count() == 1


def test_webhook_rejects_stale_events(client, square_settings, item):
    event = order_event()
    event["created_at"] = (timezone.now() - timedelta(days=10)).isoformat()
    assert signed_post(client, event).status_code == 401


def test_webhook_test_event(client, square_settings):
    response = signed_post(client, {"event_id": "t", "type": "test.webhook"})
    assert response.json()["status"] == "test_ok"


def test_refund_moves_item_back_to_intake(client, square_settings, item):
    signed_post(client, order_event())
    Sale.objects.update(square_payment_id="PAY1")
    refund = {"event_id": "evt-2", "type": "refund.updated", "created_at": timezone.now().isoformat(),
              "data": {"object": {"refund": {"status": "COMPLETED", "payment_id": "PAY1"}}}}
    assert signed_post(client, refund).status_code == 200
    item.refresh_from_db()
    assert item.status == Status.INTAKE and "refund" in item.notes.lower()


def test_inventory_webhook_zero_stock_marks_sold(client, square_settings, item):
    CatalogSync.objects.create(sku_normalized="ABC-1", square_item_id="I", square_variation_id="VAR1")
    event = {"event_id": "evt-3", "type": "inventory.count.updated", "created_at": timezone.now().isoformat(),
             "data": {"object": {"inventory_counts": [{"catalog_object_id": "VAR1", "location_id": "LOC1",
                                                        "state": "IN_STOCK", "quantity": "0",
                                                        "calculated_at": (timezone.now() + timedelta(seconds=5)).isoformat()}]}}}
    assert signed_post(client, event).status_code == 200
    item.refresh_from_db()
    assert item.status == Status.SOLD
    assert CatalogSync.objects.get().previous_status == "intake"


def test_legacy_webhook_address_still_works(client, square_settings, item):
    raw = json.dumps(order_event()).encode()
    url = "https://shop.example.com/webhooks/square/"
    sig = base64.b64encode(hmac.new(b"sig-key", url.encode() + raw, hashlib.sha256).digest()).decode()
    response = client.post("/webhooks/square.php", raw, content_type="application/json", HTTP_X_SQUARE_HMACSHA256_SIGNATURE=sig)
    assert response.status_code == 200


# ── reconciliation ───────────────────────────────────────────────────────────
def test_reconciliation_flags_and_fixes_local_problems(item):
    Item.objects.create(sku="SOLD-ON-POS", what_is_it="x")
    Sale.objects.create(sku="SOLD-ON-POS", sku_normalized="SOLD-ON-POS", square_order_id="O1", sold_at=timezone.now())
    run = reconciliation.run()
    assert run.status == "completed"
    types = set(run.issues.values_list("issue_type", flat=True))
    assert {"missing_catalog_mapping", "sold_in_square_not_marked"} <= types
    assert Item.objects.get(sku_normalized="SOLD-ON-POS").status == Status.SOLD
    summary = reconciliation.status_summary()
    assert summary["last_run"]["issues_detected"] == run.issues_detected


def test_status_and_system_endpoints(client, item):
    assert client.get(reverse("api_square_status")).json()["connected"] is False
    assert client.post(reverse("api_square_sync_all")).status_code == 400
    assert client.get(reverse("api_recon_status")).json()["last_run"] is None
