"""The Square work queue.

Saving an item never waits on Square. Instead a job is queued here and the
background worker (``python manage.py serve`` runs it automatically) pushes
it. Failed jobs are retried with growing delays, then parked as "gave up"
(dead letter) so a person can look at them.

Fix vs. the PHP version: re-queuing a SKU whose earlier job already finished
now really re-runs it (the old INSERT OR IGNORE silently dropped it).
"""
from __future__ import annotations

import random
import threading
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from core.skus import normalize_sku
from squaresync.models import SyncJob

RETRY_DELAYS_SECONDS = [30, 60, 120, 300, 600, 1800, 3600, 7200, 14400, 28800]

# The worker waits on this; enqueue() sets it so changes go out within a second.
wake_event = threading.Event()


def enqueue(sku: str, operation: str = SyncJob.Operation.CATALOG_UPSERT, priority: int = 10) -> SyncJob | None:
    sku_norm = normalize_sku(sku)
    if not sku_norm:
        return None
    now = timezone.now()
    with transaction.atomic():
        job = SyncJob.objects.select_for_update().filter(sku_normalized=sku_norm, operation=operation).first()
        if job is None:
            try:
                with transaction.atomic():
                    job = SyncJob.objects.create(sku_normalized=sku_norm, operation=operation, priority=priority)
            except IntegrityError:
                job = SyncJob.objects.get(sku_normalized=sku_norm, operation=operation)
        elif job.status == SyncJob.State.PROCESSING:
            job.rerun = True
            job.priority = max(job.priority, priority)
            job.updated_at = now
            job.save(update_fields=["rerun", "priority", "updated_at"])
        else:
            job.status = SyncJob.State.QUEUED
            job.retry_count = 0
            job.next_retry_at = None
            job.last_error = ""
            job.priority = max(job.priority, priority)
            job.updated_at = now
            job.save()
    wake_event.set()
    return job


def claim_due_jobs(limit: int = 10) -> list[SyncJob]:
    """Atomically mark up to ``limit`` due jobs as processing and return them."""
    now = timezone.now()
    with transaction.atomic():
        due = list(
            SyncJob.objects.select_for_update()
            .filter(status__in=[SyncJob.State.QUEUED, SyncJob.State.RETRYING])
            .filter(Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now))
            .order_by("-priority", "created_at")[:limit]
        )
        ids = [job.pk for job in due]
        SyncJob.objects.filter(pk__in=ids).update(
            status=SyncJob.State.PROCESSING, last_attempt_at=now, updated_at=now, rerun=False
        )
    return list(SyncJob.objects.filter(pk__in=ids).order_by("-priority", "created_at"))


def mark_completed(job: SyncJob) -> None:
    job.refresh_from_db()
    job.updated_at = timezone.now()
    if job.rerun:
        job.status = SyncJob.State.QUEUED
        job.rerun = False
        job.next_retry_at = None
    else:
        job.status = SyncJob.State.COMPLETED
        job.last_error = ""
    job.save()


def mark_failed(job: SyncJob, error: str) -> None:
    job.refresh_from_db()
    job.retry_count += 1
    job.last_error = error[:2000]
    job.updated_at = timezone.now()
    if job.retry_count >= job.max_retries:
        job.status = SyncJob.State.DEAD_LETTER
        job.next_retry_at = None
    else:
        job.status = SyncJob.State.RETRYING
        job.next_retry_at = timezone.now() + timedelta(seconds=next_retry_delay(job.retry_count))
    if job.rerun:
        # It changed again while failing — try the fresh data soon.
        job.rerun = False
        job.next_retry_at = timezone.now() + timedelta(seconds=5)
        job.status = SyncJob.State.RETRYING
    job.save()


def release_unfinished() -> int:
    """Put jobs left 'processing' by a crash or restart back in the queue."""
    return SyncJob.objects.filter(status=SyncJob.State.PROCESSING).update(
        status=SyncJob.State.QUEUED, updated_at=timezone.now()
    )


def next_retry_delay(retry_count: int) -> int:
    index = min(max(retry_count, 1) - 1, len(RETRY_DELAYS_SECONDS) - 1)
    return RETRY_DELAYS_SECONDS[index] + random.randint(0, 30)


def reset_dead_letters(sku: str | None = None) -> int:
    qs = SyncJob.objects.filter(status=SyncJob.State.DEAD_LETTER)
    if sku:
        qs = qs.filter(sku_normalized=normalize_sku(sku))
    count = qs.update(status=SyncJob.State.QUEUED, retry_count=0, last_error="", next_retry_at=None, updated_at=timezone.now())
    if count:
        wake_event.set()
    return count


def stats() -> dict[str, int]:
    counts = {state: 0 for state in SyncJob.State.values}
    for row in SyncJob.objects.values("status").annotate(n=Count("id")):
        counts[row["status"]] = row["n"]
    return counts
