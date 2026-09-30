"""Database backups: consistent snapshots, checksums, mirrors and verification.

``VACUUM INTO`` makes a clean, consistent copy of the live database even
while people are saving — copying the .sqlite3 file directly can miss recent
writes or produce a corrupt copy, so never do that.
"""
from __future__ import annotations

import hashlib
import logging
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.db import connection

logger = logging.getLogger("pinksheet")
PREFIX = "pinksheet"


@dataclass
class BackupResult:
    ok: bool
    path: Path | None = None
    messages: list[str] = field(default_factory=list)
    error: str = ""


def backup_dir() -> Path:
    path = Path(settings.BACKUP_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def list_backups() -> list[Path]:
    return sorted(backup_dir().glob(f"{PREFIX}-*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)


def latest_backup() -> Path | None:
    backups = list_backups()
    return backups[0] if backups else None


def snapshot() -> Path:
    """Write ``pinksheet-YYYYmmdd-HHMMSS.sqlite3`` plus a ``.sha256`` file."""
    target = backup_dir() / f"{PREFIX}-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
    if target.exists():
        target = target.with_name(target.stem + f"-{datetime.now():%f}.sqlite3")
    connection.ensure_connection()
    with connection.cursor() as cursor:
        cursor.execute("VACUUM INTO %s", [str(target)])
    target.with_name(target.name + ".sha256").write_text(sha256_of(target), encoding="utf-8")
    return target


def _mirror_file(source: Path, mirror_dir: Path) -> None:
    mirror_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, mirror_dir / source.name)
    checksum = source.with_name(source.name + ".sha256")
    if checksum.exists():
        shutil.copy2(checksum, mirror_dir / checksum.name)


def mirror_photos(target: Path) -> int:
    """Copy new or changed photo files to the mirror folder. Returns files copied."""
    source_root = Path(settings.MEDIA_ROOT)
    copied = 0
    for folder in ("sku_photos", "ebay_images", "doc_photos"):
        root = source_root / folder
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            dest = target / folder / path.relative_to(root)
            if dest.exists() and dest.stat().st_size == path.stat().st_size and dest.stat().st_mtime >= path.stat().st_mtime:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            copied += 1
    return copied


def prune(keep: int) -> int:
    if keep <= 0:
        return 0
    removed = 0
    for old in list_backups()[keep:]:
        for path in (old, old.with_name(old.name + ".sha256")):
            if path.exists():
                path.unlink()
        removed += 1
    return removed


def run_backup() -> BackupResult:
    config = settings.PINKSHEET
    result = BackupResult(ok=True)
    try:
        path = snapshot()
    except Exception as exc:  # noqa: BLE001 — report any failure to the operator
        logger.exception("Backup snapshot failed")
        return BackupResult(ok=False, error=f"Snapshot failed: {exc}")
    result.path = path
    result.messages.append(f"Backup created: {path.name}")

    if config["BACKUP_MIRROR_DIR"]:
        try:
            _mirror_file(path, Path(config["BACKUP_MIRROR_DIR"]))
            result.messages.append(f"Copied to {config['BACKUP_MIRROR_DIR']}")
        except OSError as exc:
            result.ok = False
            result.error = f"Mirror copy failed: {exc}"
            result.messages.append(result.error)
    if config["BACKUP_PHOTOS_MIRROR"]:
        try:
            copied = mirror_photos(Path(config["BACKUP_PHOTOS_MIRROR"]))
            result.messages.append(f"Photo mirror updated ({copied} new file(s))")
        except OSError as exc:
            result.ok = False
            result.error = f"Photo mirror failed: {exc}"
            result.messages.append(result.error)
    removed = prune(config["BACKUP_KEEP"])
    if removed:
        result.messages.append(f"Removed {removed} old backup(s)")
    logger.info("Backup finished: %s", "; ".join(result.messages))
    return result


def integrity_ok(path: Path) -> bool:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            con.close()
    except sqlite3.Error:
        return False


def checksum_ok(path: Path) -> bool:
    checksum_file = path.with_name(path.name + ".sha256")
    if not checksum_file.exists():
        return True
    return checksum_file.read_text(encoding="utf-8").strip() == sha256_of(path)


def verify_latest() -> dict:
    live = Path(connection.settings_dict["NAME"])
    messages, ok = [], True
    if not integrity_ok(live):
        ok = False
        messages.append("Live database failed its integrity check")
    latest = latest_backup()
    if latest is None:
        messages.append("No backup found yet")
    else:
        if not checksum_ok(latest):
            ok = False
            messages.append(f"{latest.name}: checksum does not match")
        if not integrity_ok(latest):
            ok = False
            messages.append(f"{latest.name}: integrity check failed")
        if ok:
            messages.append(f"{latest.name} verified")
    return {"ok": ok, "latest_backup": latest.name if latest else None, "messages": messages}


def backup_status() -> dict:
    latest = latest_backup()
    usage = shutil.disk_usage(backup_dir())
    info = {
        "latest": None,
        "age_hours": None,
        "size_bytes": None,
        "count": len(list_backups()),
        "free_percent": round(usage.free / usage.total * 100, 1) if usage.total else None,
    }
    if latest:
        stat = latest.stat()
        info.update(
            latest=latest.name,
            age_hours=round((datetime.now().timestamp() - stat.st_mtime) / 3600, 1),
            size_bytes=stat.st_size,
        )
    return info


def restore(backup: Path) -> Path:
    """Replace the live database with ``backup``. Stop the server first!

    A safety copy of the current database is written next to it before anything changes.
    """
    live = Path(connection.settings_dict["NAME"])
    if not integrity_ok(backup):
        raise RuntimeError(f"{backup.name} failed its integrity check; not restoring it.")
    if not checksum_ok(backup):
        raise RuntimeError(f"{backup.name} does not match its checksum; not restoring it.")
    connection.close()
    safety = live.with_name(f"{live.stem}-before-restore-{datetime.now():%Y%m%d-%H%M%S}.sqlite3")
    if live.exists():
        src = sqlite3.connect(str(live))
        try:
            src.execute("VACUUM INTO ?", (str(safety),))
        finally:
            src.close()
    for suffix in ("-wal", "-shm"):
        side = live.with_name(live.name + suffix)
        if side.exists():
            side.unlink()
    shutil.copy2(backup, live)
    return safety
