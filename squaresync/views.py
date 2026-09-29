"""Square endpoints: status for the dashboard, manual actions, and the webhook receiver."""
import json
import logging

from django.db import close_old_connections
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.http import json_error, json_ok
from core.network import private_network_only
from inventory.models import Item
from squaresync import full_sync
from squaresync import queue as square_queue
from squaresync import reconciliation
from squaresync import sync as square_sync
from squaresync import webhooks
from squaresync.client import log_event
from squaresync.config import get_config
from squaresync.models import CatalogSync, Sale, SyncAuditLog, SyncJob, WebhookEvent

logger = logging.getLogger("pinksheet.square")


@require_GET
def status(request):
    config = get_config()
    today = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    stats = square_queue.stats()
    last_sync = CatalogSync.objects.exclude(last_synced_at=None).order_by("-last_synced_at").first()
    last_error = (
        CatalogSync.objects.exclude(last_error="")
        .filter(sku_normalized__in=Item.objects.values("sku_normalized"))
        .order_by("-updated_at")
        .first()
    )
    recent_sale = Sale.objects.first()
    return json_ok(
        connected=config.enabled,
        environment=config.environment,
        missing=config.missing(),
        last_sync_at=timezone.localtime(last_sync.last_synced_at).strftime("%Y-%m-%d %H:%M") if last_sync else None,
        last_error=last_error.last_error if last_error else None,
        queue_waiting=stats["queued"] + stats["retrying"] + stats["processing"],
        queue_dead_letter=stats["dead_letter"],
        webhooks_today=WebhookEvent.objects.filter(processed_at__gte=today).count(),
        webhooks_failed_today=SyncAuditLog.objects.filter(timestamp__gte=today, direction="pull", status="failure").count(),
        sold_today=Sale.objects.filter(sold_at__gte=today).count(),
        recent_sale=None if recent_sale is None else {
            "sku": recent_sale.sku_normalized,
            "price": f"{recent_sale.sale_price:.2f}",
            "sold_at": timezone.localtime(recent_sale.sold_at).strftime("%Y-%m-%d %H:%M"),
        },
    )


@require_POST
@private_network_only
def sync_all(request):
    """Start pushing every SKU to Square (the dashboard's "Sync Square now" button).

    It runs in the background; the page follows it with sync_all_progress. Clicking
    again while it runs returns the running sync's progress instead of a second sync.
    """
    config = get_config()
    if not config.enabled:
        return json_error("Square isn't set up yet. Add SQUARE_ACCESS_TOKEN and SQUARE_LOCATION_ID to .env.")
    started, state = full_sync.start(config, started_by=getattr(request, "actor", ""))
    return json_ok(started=started, **state)


@require_GET
def sync_all_progress(request):
    return json_ok(**full_sync.progress())


@require_POST
@private_network_only
def test_connection(request):
    result = square_sync.test_connection()
    return JsonResponse(result, status=200 if result["ok"] else 400)


@require_POST
@private_network_only
def retry_dead_letters(request):
    return json_ok(reset=square_queue.reset_dead_letters())


@require_POST
@private_network_only
def queue_everything(request):
    """Queue a full sync of every SKU; the background worker works through them."""
    count = 0
    for sku in Item.objects.exclude(sku_normalized="").values_list("sku_normalized", flat=True):
        square_queue.enqueue(sku, SyncJob.Operation.CATALOG_UPSERT, priority=0)
        count += 1
    return json_ok(queued=count)


@require_GET
def reconciliation_status(request):
    return json_ok(**reconciliation.status_summary())


@require_POST
@private_network_only
def reconciliation_run(request):
    dry_run = request.POST.get("dry_run") == "1"
    close_old_connections()
    run = reconciliation.run(dry_run=dry_run)
    if run.status != "completed":
        return json_error(f"Reconciliation failed: {run.error_message}", 500)
    return json_ok(
        run_id=run.pk,
        detected=run.issues_detected,
        repaired=run.issues_repaired,
        manual=run.manual_actions_required,
        message=(
            f"{run.issues_detected} issue(s) found, {run.issues_repaired} fixed, "
            f"{run.manual_actions_required} need a person."
        ),
    )


def _response(status_code: int, **body) -> JsonResponse:
    return JsonResponse(body, status=status_code)


@csrf_exempt
def webhook(request):
    """Square → Dispodex. Registered in Square as https://<public address>/webhooks/square/."""
    config = get_config()
    if request.method != "POST":
        return _response(405, ok=False, error="Method not allowed")
    if not config.webhooks_enabled:
        return _response(503, ok=False, error="Webhooks are not configured")
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length > config.webhook_max_body_bytes:
        return _response(413, ok=False, error="Request body too large")
    signature = (
        request.headers.get("X-Square-Hmacsha256-Signature")
        or request.headers.get("X-Square-Hmac-Sha256-Signature")
        or ""
    )
    if not signature:
        return _response(401, ok=False, error="Missing signature header")
    raw = request.body
    if not raw:
        return _response(400, ok=False, error="Empty request body")
    if len(raw) > config.webhook_max_body_bytes:
        return _response(413, ok=False, error="Request body too large")
    if not webhooks.verify_signature(raw, signature, config.webhook_signature_key, config.webhook_notification_url):
        log_event(operation="webhook", status="rejected", message="invalid signature",
                  remote=request.META.get("REMOTE_ADDR"), url=config.webhook_notification_url)
        return _response(401, ok=False, error="Invalid signature")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return _response(400, ok=False, error="Invalid JSON")
    if not isinstance(body, dict):
        return _response(400, ok=False, error="Invalid JSON")
    if not webhooks.is_fresh(body, config.webhook_max_age_seconds):
        return _response(401, ok=False, error="Webhook event is outside the replay window")
    event_type = str(body.get("type") or "")
    if event_type in ("test.webhook", "webhook.test"):
        log_event(operation=event_type, status="test_ok", webhook_id=body.get("event_id"))
        return _response(200, ok=True, status="test_ok")
    try:
        result = webhooks.handle_event(body)
    except Exception:  # noqa: BLE001 — Square retries on 5xx, so report failure
        logger.exception("Webhook %s failed", event_type)
        return _response(500, ok=False, error="Internal server error")
    if result["status"] == "error":
        return _response(500, ok=False, error=result["message"])
    return _response(200, ok=True, status=result["status"], message=result.get("message", ""))
