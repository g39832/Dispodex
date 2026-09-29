"""The background worker: Square queue, nightly backup and daily reconciliation.

``python manage.py serve`` starts this in a thread next to the web server, so
there is only one thing to run. ``python manage.py run_worker`` runs it on
its own (handy while developing with ``runserver``).

Only one worker may run at a time; a lock file in data/ enforces that even
if someone starts the server twice.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from operations import backups
from operations.models import SystemState
from squaresync import processor
from squaresync import queue as square_queue
from squaresync import reconciliation
from squaresync.config import get_config
from squaresync.models import ReconciliationRun

logger = logging.getLogger("pinksheet")

LOOP_SECONDS = 15
HEARTBEAT_KEY = "worker_heartbeat"
LAST_BACKUP_KEY = "last_scheduled_backup"
LAST_RECON_KEY = "last_scheduled_reconciliation"


class _FileLock:
    """Exclusive, non-blocking lock on a file (Windows and Linux)."""

    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+")  # noqa: SIM115 — held open for the life of the worker
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            return False
        return True


def _due_daily(key: str, hour: int, now: datetime) -> bool:
    """True once per day, at or after ``hour`` local time."""
    if now.hour < hour:
        return False
    last = SystemState.get(key)
    return last != now.date().isoformat()


def tick() -> None:
    """One pass of every scheduled duty. Safe to call as often as you like."""
    now = timezone.localtime()
    SystemState.set(HEARTBEAT_KEY, timezone.now().isoformat())

    result = processor.process_queue(limit=20)
    if result["processed"]:
        logger.info("Square queue: %s processed, %s ok, %s failed", result["processed"], result["ok"], result["failed"])

    config = settings.PINKSHEET
    if _due_daily(LAST_BACKUP_KEY, config["BACKUP_HOUR"], now):
        latest = backups.latest_backup()
        recent = latest and (time.time() - latest.stat().st_mtime) < 12 * 3600
        if not recent:
            outcome = backups.run_backup()
            logger.info("Scheduled backup: %s", "ok" if outcome.ok else outcome.error)
        SystemState.set(LAST_BACKUP_KEY, now.date().isoformat())

    if get_config().enabled and _due_daily(LAST_RECON_KEY, config["RECONCILE_HOUR"], now):
        run = reconciliation.run(trigger=ReconciliationRun.Trigger.SCHEDULED)
        logger.info("Scheduled reconciliation #%s: %s", run.pk, run.status)
        SystemState.set(LAST_RECON_KEY, now.date().isoformat())


def worker_alive(max_age_seconds: int = LOOP_SECONDS * 4) -> bool:
    beat = SystemState.get(HEARTBEAT_KEY)
    if not beat:
        return False
    try:
        return timezone.now() - datetime.fromisoformat(beat) < timedelta(seconds=max_age_seconds)
    except ValueError:
        return False


class Worker(threading.Thread):
    def __init__(self):
        super().__init__(name="pinksheet-worker", daemon=True)
        # Not "_stop": threading.Thread uses that name internally (join/is_alive call it).
        self._stop_event = threading.Event()
        self._lock = _FileLock(Path(settings.DATA_DIR) / ".worker.lock")

    def stop(self) -> None:
        self._stop_event.set()
        square_queue.wake_event.set()

    def run(self) -> None:
        if not self._lock.acquire():
            logger.warning("Another Dispodex worker is already running; this one will stay idle.")
            return
        logger.info("Background worker started.")
        close_old_connections()
        square_queue.release_unfinished()
        while not self._stop_event.is_set():
            try:
                close_old_connections()
                tick()
            except Exception:  # noqa: BLE001 — log and keep the worker alive
                logger.exception("Background worker tick failed")
            finally:
                close_old_connections()
            square_queue.wake_event.wait(LOOP_SECONDS)
            square_queue.wake_event.clear()
        logger.info("Background worker stopped.")
