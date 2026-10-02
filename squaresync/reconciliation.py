"""Inventory reconciliation: find where Dispodex and Square disagree and fix what is safe.

Runs daily from the background worker and on demand from the dashboard.
Every problem found is saved as a ReconciliationIssue; issues that can be
fixed automatically are repaired straight away, the rest are flagged for a
person to review.
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import timedelta

from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone

from inventory.models import Item, Photo, Status
from inventory.services import photos as photo_service
from inventory.models import ItemEvent
from inventory.services import history
from squaresync import queue as square_queue
from squaresync import sync as square_sync
from squaresync.client import SquareClient, SquareError
from squaresync.config import get_config
from squaresync.models import (
    CatalogSync,
    ReconciliationAlert,
    ReconciliationIssue,
    ReconciliationRun,
    Sale,
    SyncJob,
)

logger = logging.getLogger("pinksheet.square")
Severity = ReconciliationIssue.Severity


def _issue(run, sku, issue_type, severity, description, repair_action="", auto=False, pinksheet_value="", square_value=""):
    ReconciliationIssue.objects.create(
        run=run, sku_normalized=sku, issue_type=issue_type, severity=severity, description=description,
        repair_action=repair_action, auto_repairable=auto, pinksheet_value=pinksheet_value, square_value=square_value,
    )


# ── checks (local only) ──────────────────────────────────────────────────────
def check_missing_mappings(run) -> int:
    mapped = CatalogSync.objects.filter(sku_normalized=OuterRef("sku_normalized"))
    items = Item.objects.exclude(sku_normalized="").annotate(has_map=Exists(mapped)).filter(has_map=False)
    count = 0
    for item in items:
        severity = Severity.WARNING if (item.price or 0) > 0 else Severity.INFO
        _issue(run, item.sku_normalized, "missing_catalog_mapping", severity,
               f"SKU {item.sku_normalized} has no Square catalog item yet", "catalog_upsert", True)
        count += 1
    return count


def check_never_synced(run) -> int:
    count = 0
    for mapping in CatalogSync.objects.filter(last_synced_at__isnull=True, square_item_id=""):
        _issue(run, mapping.sku_normalized, "never_synced", Severity.WARNING,
               f"SKU {mapping.sku_normalized} has never been pushed to Square", "full_sync", True)
        count += 1
    return count


def check_stuck(run) -> int:
    count = 0
    day_ago = timezone.now() - timedelta(days=1)
    stuck = CatalogSync.objects.exclude(last_error="").filter(Q(last_synced_at__isnull=True) | Q(last_synced_at__lt=day_ago))
    for mapping in stuck.order_by("-updated_at")[:200]:
        _issue(run, mapping.sku_normalized, "stuck_retry", Severity.WARNING,
               f"SKU {mapping.sku_normalized} keeps failing to sync: {mapping.last_error}", "full_sync", True,
               pinksheet_value=mapping.last_error)
        count += 1
    for job in SyncJob.objects.filter(status=SyncJob.State.DEAD_LETTER).order_by("-updated_at")[:100]:
        _issue(run, job.sku_normalized, "queue_dead_letter", Severity.WARNING,
               f"Square gave up on {job.get_operation_display().lower()} for {job.sku_normalized}: {job.last_error}",
               "reset_queue", True, pinksheet_value=f"retries: {job.retry_count}")
        count += 1
    return count


def check_pending_updates(run) -> int:
    count = 0
    mappings = {m.sku_normalized: m for m in CatalogSync.objects.filter(last_synced_at__isnull=False, last_error="")}
    for item in Item.objects.filter(sku_normalized__in=mappings.keys()):
        mapping = mappings[item.sku_normalized]
        if mapping.payload_hash and mapping.payload_hash != square_sync.payload_hash(item, photo_service.preferred_photo(item.sku_normalized)):
            _issue(run, item.sku_normalized, "pending_update", Severity.INFO,
                   f"SKU {item.sku_normalized} changed since its last Square sync", "full_sync", True)
            count += 1
    return count


def check_sold_not_marked(run) -> int:
    count = 0
    sold_skus = set(Sale.objects.values_list("sku_normalized", flat=True))
    for item in Item.objects.filter(sku_normalized__in=sold_skus).exclude(status=Status.SOLD):
        _issue(run, item.sku_normalized, "sold_in_square_not_marked", Severity.CRITICAL,
               f"SKU {item.sku_normalized} was sold on Square but is '{item.status_label}' here",
               "mark_sold", True, pinksheet_value=f"status={item.status}", square_value="sale recorded")
        count += 1
    return count


def check_missing_images(run) -> int:
    count = 0
    unmapped_image = CatalogSync.objects.filter(sku_normalized=OuterRef("sku_normalized")).exclude(square_image_id="")
    rows = (
        Photo.objects.values("sku_normalized")
        .annotate(n=Count("id"), has_image=Exists(unmapped_image))
        .filter(has_image=False)
        .order_by("-n")[:100]
    )
    live = set(Item.objects.values_list("sku_normalized", flat=True))
    for row in rows:
        if row["sku_normalized"] not in live:
            continue
        _issue(run, row["sku_normalized"], "missing_image", Severity.WARNING,
               f"SKU {row['sku_normalized']} has {row['n']} photo(s) but none in Square", "full_sync", True,
               pinksheet_value=f"photos: {row['n']}")
        count += 1
    return count


# ── check that talks to Square ────────────────────────────────────────────────
def check_orphans(run, client: SquareClient) -> tuple[int, int]:
    """Square items whose SKU no longer exists in Dispodex, and duplicate SKUs in Square."""
    detected, failed = 0, 0
    square_skus: dict[str, list[dict]] = {}
    cursor = None
    try:
        while True:
            body = {"include_deleted_objects": False, "object_types": ["ITEM"], "limit": 200}
            if cursor:
                body["cursor"] = cursor
            resp = client.post("/v2/catalog/search", body)
            for obj in resp.get("objects") or []:
                for variation in (obj.get("item_data") or {}).get("variations") or []:
                    sku = str((variation.get("item_variation_data") or {}).get("sku") or "").strip().upper()
                    if sku:
                        square_skus.setdefault(sku, []).append(
                            {"item_id": obj.get("id", ""), "name": (obj.get("item_data") or {}).get("name", "")}
                        )
            cursor = resp.get("cursor")
            if not cursor:
                break
    except SquareError as exc:
        logger.warning("Reconciliation catalog fetch failed: %s", exc)
        return detected, failed + 1

    live = set(Item.objects.values_list("sku_normalized", flat=True))
    for sku, entries in square_skus.items():
        if sku not in live:
            _issue(run, sku, "orphaned_square_item", Severity.WARNING,
                   f"Square item '{sku}' ({entries[0]['name']}) has no Dispodex record", "manual_review", False,
                   square_value=f"item_id={entries[0]['item_id']}")
            detected += 1
        if len({e["item_id"] for e in entries}) > 1:
            _issue(run, sku, "duplicate_catalog_item", Severity.CRITICAL,
                   f"SKU {sku} exists on {len(entries)} different Square items", "manual_review", False,
                   square_value=", ".join(e["item_id"] for e in entries))
            detected += 1
    return detected, failed


# ── repairs ──────────────────────────────────────────────────────────────────
def repair(issue: ReconciliationIssue, client: SquareClient | None) -> tuple[str, str]:
    """Returns (repair_status, message)."""
    action, sku = issue.repair_action, issue.sku_normalized
    if action in ("catalog_upsert", "full_sync"):
        result = square_sync.sync_item(sku, client=client)
        if not result.succeeded:
            square_queue.enqueue(sku)  # the worker keeps retrying with backoff
        return ("auto_repaired" if result.succeeded else "failed", f"{result.message} for {sku}")
    if action == "mark_sold":
        if not Sale.objects.filter(sku_normalized=sku).exists():
            return "manual_required", f"No sale record for {sku} — check before marking it sold"
        item = Item.objects.filter(sku_normalized=sku).first()
        if item and item.status != Status.SOLD:
            before = history.snapshot(item)
            item.apply_status(Status.SOLD)
            item.save()
            history.record(item, ItemEvent.Action.EDITED, before=before, actor="Square check", note="Matched a Square sale")
            square_queue.enqueue(sku, SyncJob.Operation.INVENTORY_SET)
            return "auto_repaired", f"Marked {sku} as sold"
        return "auto_repaired", f"{sku} was already sold"
    if action == "reset_queue":
        square_queue.reset_dead_letters(sku or None)
        return "auto_repaired", f"Queue reset for {sku or 'all SKUs'}"
    return "manual_required", f"Needs manual review: {issue.description}"


def run(trigger: str = ReconciliationRun.Trigger.ONDEMAND, dry_run: bool = False, fetch_catalog: bool = True) -> ReconciliationRun:
    started = time.monotonic()
    run_obj = ReconciliationRun.objects.create(trigger_type=trigger)
    config = get_config()
    client = SquareClient(config) if config.enabled else None
    try:
        detected = (
            check_missing_mappings(run_obj)
            + check_never_synced(run_obj)
            + check_stuck(run_obj)
            + check_pending_updates(run_obj)
            + check_sold_not_marked(run_obj)
            + check_missing_images(run_obj)
        )
        api_failed = 0
        if fetch_catalog and client:
            found, api_failed = check_orphans(run_obj, client)
            detected += found

        repaired = manual = failed = 0
        order = {"critical": 0, "warning": 1, "info": 2}
        pending = sorted(run_obj.issues.filter(auto_repairable=True), key=lambda i: (order.get(i.severity, 3), i.pk))
        for issue in pending:
            if dry_run:
                issue.repair_status, issue.repair_result = "skipped", f"Dry run — would run {issue.repair_action}"
            elif client is None and issue.repair_action in ("catalog_upsert", "full_sync"):
                issue.repair_status, issue.repair_result = "skipped", "Square is not configured yet"
            else:
                issue.repair_status, issue.repair_result = repair(issue, client)
                issue.repaired_at = timezone.now()
            issue.save()
            repaired += issue.repair_status == "auto_repaired"
            failed += issue.repair_status == "failed"
            manual += issue.repair_status == "manual_required"
        manual += run_obj.issues.filter(auto_repairable=False).count()

        issues = list(run_obj.issues.all())
        breakdown: dict[str, dict] = {}
        for issue in issues:
            entry = breakdown.setdefault(issue.issue_type, {"total": 0, "repaired": 0, "failed": 0, "pending": 0})
            entry["total"] += 1
            key = {"auto_repaired": "repaired", "failed": "failed"}.get(issue.repair_status, "pending")
            entry[key] += 1
        run_obj.breakdown = breakdown
        run_obj.error_summary = [
            {"type": i.issue_type, "sku": i.sku_normalized, "description": i.description, "result": i.repair_result}
            for i in issues if i.repair_status in ("failed", "manual_required") or not i.auto_repairable
        ][:200]
        run_obj.total_items_checked = Item.objects.exclude(sku_normalized="").count()
        run_obj.issues_detected = detected
        run_obj.issues_repaired = repaired
        run_obj.manual_actions_required = manual
        run_obj.api_requests_made = client.requests_made if client else 0
        run_obj.api_requests_failed = api_failed
        run_obj.status = ReconciliationRun.State.COMPLETED
        if failed:
            ReconciliationAlert.objects.create(alert_type="repair_failures", severity="warning",
                                               title=f"{failed} repair(s) failed during reconciliation",
                                               description=f"Run #{run_obj.pk} had {failed} failed repairs.")
        if manual:
            ReconciliationAlert.objects.create(alert_type="manual_actions_required", severity="warning",
                                               title=f"{manual} issue(s) need a person to review",
                                               description=f"Run #{run_obj.pk} found items needing attention.")
    except Exception as exc:  # noqa: BLE001 — record any failure on the run itself
        logger.exception("Reconciliation run %s failed", run_obj.pk)
        run_obj.status = ReconciliationRun.State.FAILED
        run_obj.error_message = str(exc)[:2000]
        ReconciliationAlert.objects.create(alert_type="reconciliation_failed", severity="critical",
                                           title="Reconciliation run failed", description=str(exc)[:2000])
    run_obj.completed_at = timezone.now()
    run_obj.runtime_seconds = round(time.monotonic() - started, 3)
    run_obj.save()
    return run_obj


def status_summary() -> dict:
    last = ReconciliationRun.objects.first()
    pending = ReconciliationIssue.objects.filter(
        repair_status__in=["pending", "failed", "manual_required"], run=last
    ) if last else ReconciliationIssue.objects.none()
    alerts = ReconciliationAlert.objects.filter(dismissed=False)
    return {
        "connected": get_config().enabled,
        "is_running": bool(last and last.status == ReconciliationRun.State.RUNNING),
        "last_run": None if last is None else {
            "id": last.pk,
            "trigger_type": last.trigger_type,
            "status": last.status,
            "started_at": timezone.localtime(last.started_at).strftime("%Y-%m-%d %H:%M"),
            "completed_at": timezone.localtime(last.completed_at).strftime("%Y-%m-%d %H:%M") if last.completed_at else "",
            "issues_detected": last.issues_detected,
            "issues_repaired": last.issues_repaired,
            "runtime_seconds": last.runtime_seconds,
        },
        "pending_issues": pending.count(),
        "alerts_total": alerts.count(),
        "alerts_critical": alerts.filter(severity="critical").count(),
        "issues": [
            {"sku": i.sku_normalized, "severity": i.severity, "description": i.description, "status": i.repair_status}
            for i in pending.order_by("id")[:10]
        ],
        "breakdown": dict(Counter({k: v["total"] for k, v in (last.breakdown if last else {}).items()})),
    }
