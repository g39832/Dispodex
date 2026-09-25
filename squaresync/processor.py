"""Run queued Square jobs (called by the background worker and by a management command)."""
from __future__ import annotations

import logging
import time

from squaresync import queue as square_queue
from squaresync import sync as square_sync
from squaresync.client import SquareClient
from squaresync.config import get_config
from squaresync.models import SyncAuditLog, SyncJob

logger = logging.getLogger("pinksheet.square")


def run_job(job: SyncJob, client: SquareClient | None = None) -> square_sync.SyncResult:
    op = job.operation
    if op in (SyncJob.Operation.CATALOG_UPSERT, SyncJob.Operation.FULL_SYNC):
        return square_sync.sync_item(job.sku_normalized, client=client)
    if op == SyncJob.Operation.INVENTORY_SET:
        return square_sync.push_inventory(job.sku_normalized, client=client)
    if op == SyncJob.Operation.INVENTORY_PULL:
        return square_sync.pull_inventory(job.sku_normalized, client=client)
    return square_sync.SyncResult("error", f"Unknown operation: {op}")


def process_queue(limit: int = 10) -> dict:
    """Process up to ``limit`` due jobs. Jobs wait untouched while Square isn't configured."""
    config = get_config()
    summary = {"processed": 0, "ok": 0, "failed": 0, "waiting_for_config": not config.enabled}
    if not config.enabled:
        return summary
    client = SquareClient(config)
    for job in square_queue.claim_due_jobs(limit):
        started = time.monotonic()
        try:
            result = run_job(job, client)
        except Exception as exc:  # noqa: BLE001 — a bad job must never stop the worker
            logger.exception("Square job %s crashed", job.pk)
            result = square_sync.SyncResult("error", f"Unexpected error: {exc}")
        duration = int((time.monotonic() - started) * 1000)
        summary["processed"] += 1
        if result.succeeded:
            square_queue.mark_completed(job)
            summary["ok"] += 1
        else:
            square_queue.mark_failed(job, result.message)
            summary["failed"] += 1
        SyncAuditLog.objects.create(
            operation=job.operation,
            sku_normalized=job.sku_normalized,
            direction="push",
            status="success" if result.succeeded else "failure",
            duration_ms=duration,
            queue_job_id=job.pk,
            retry_count=job.retry_count,
            object_type="ITEM_VARIATION" if job.operation == SyncJob.Operation.INVENTORY_SET else "ITEM",
            response_summary=result.message[:2000] if result.succeeded else "",
            error_message="" if result.succeeded else result.message[:2000],
        )
    return summary
