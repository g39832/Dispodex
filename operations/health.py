"""System health checks shown on the System page and returned by /api/health/."""
from __future__ import annotations

import io
import platform
import shutil
import sys
import tempfile
from pathlib import Path

import django
from django.conf import settings
from django.db import connection
from PIL import Image

from inventory.models import Item, Photo
from inventory.services import photos as photo_service
from operations import backups, worker
from squaresync import queue as square_queue
from squaresync.config import get_config


def _dir_status(label: str, path: Path) -> dict:
    exists = path.exists()
    writable = False
    if exists:
        try:
            with tempfile.NamedTemporaryFile(dir=path, delete=True):
                writable = True
        except OSError:
            writable = False
    free = shutil.disk_usage(path).free if exists else None
    return {"label": label, "path": str(path), "exists": exists, "writable": writable, "free_bytes": free}


def image_pipeline_ok() -> tuple[bool, str]:
    """Round-trip a tiny image through the real upload pipeline."""
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (58, 120, 194)).save(buffer, "JPEG")
    buffer.seek(0)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            stored = photo_service.store_image(buffer, Path(tmp))
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
        return True, f"stored as {stored.mime_type}"


def quick_status() -> dict:
    backup = backups.backup_status()
    return {
        "status": "ok",
        "maintenance": settings.PINKSHEET["MAINTENANCE_MODE"],
        "items": Item.objects.count(),
        "backup": {
            "latest": backup["latest"],
            "age_hours": backup["age_hours"],
            "stale": backup["age_hours"] is None or backup["age_hours"] > settings.PINKSHEET["BACKUP_STALE_HOURS"],
        },
        "worker_running": worker.worker_alive(),
        "square_configured": get_config().enabled,
    }


def full_report() -> dict:
    with connection.cursor() as cursor:
        cursor.execute("select sqlite_version()")
        sqlite_version = cursor.fetchone()[0]
    pipeline_ok, pipeline_detail = image_pipeline_ok()
    config = get_config()
    db_path = Path(connection.settings_dict["NAME"])
    return {
        "environment": {
            "Python": sys.version.split()[0],
            "Django": django.get_version(),
            "SQLite": sqlite_version,
            "Pillow": Image.__version__,
            "OS": f"{platform.system()} {platform.release()}",
            "Time zone": settings.TIME_ZONE,
            "Debug mode": "on" if settings.DEBUG else "off",
            "Sign-in required": "yes" if settings.PINKSHEET["REQUIRE_LOGIN"] else "no",
        },
        "storage": [
            _dir_status("Data folder", Path(settings.DATA_DIR)),
            _dir_status("Photos", photo_service.photo_root()),
            _dir_status("Backups", Path(settings.BACKUP_DIR)),
            _dir_status("Logs", Path(settings.LOG_DIR)),
        ],
        "database": {
            "path": str(db_path),
            "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
            "items": Item.objects.count(),
            "photos": Photo.objects.count(),
            "integrity_ok": backups.integrity_ok(db_path),
        },
        "pipeline": {"ok": pipeline_ok, "detail": pipeline_detail},
        "backup": backups.backup_status(),
        "worker_running": worker.worker_alive(),
        "square": {
            "enabled": config.enabled,
            "environment": config.environment,
            "missing": config.missing(),
            "webhooks_enabled": config.webhooks_enabled,
            "webhook_missing": config.missing_webhook(),
            "webhook_url": config.webhook_notification_url,
            "queue": square_queue.stats(),
        },
    }
