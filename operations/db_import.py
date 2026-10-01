"""The System page's "Import database" button.

Databases are recognised by their tables rather than their name:

* a **Dispodex database** (``pinksheet.sqlite3``, or any file from ``data/backups``,
  e.g. from another computer): it replaces the current database;
* an **old PHP Pinksheet database** (``intake.sqlite``): its items, photos list,
  drafts, scripts, archive and Square records are copied in by the legacy importer.

Anything else that is a common table format (CSV, TSV, Excel .xlsx, JSON) is
merged in row by row by ``operations.sheet_import``: new SKUs are added and
known SKUs updated, the rest is left alone.

Every time a verified backup is made first, so an import can always be undone
with ``manage.py restore_backup``. Photo files live on disk, not in the
database, so they are not part of an import.
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone

from inventory.models import Item
from inventory.services import photos as photo_service
from operations import backups, excel_import, sheet_import
from operations.legacy_import import run_import
from operations.models import SystemState

logger = logging.getLogger("pinksheet")

LAST_IMPORT_KEY = "last_database_import"
SQLITE_HEADER = b"SQLite format 3\x00"
_running = threading.Lock()


class ImportRefused(Exception):
    """The file can't be imported; nothing was changed. The message is shown to the person."""


@dataclass
class ImportOutcome:
    kind: str
    items: int
    backup: str
    messages: list[str] = field(default_factory=list)


def _tables(path: Path) -> set[str]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        con.close()


def detect_kind(path: Path) -> str:
    """'dispodex', 'legacy' or 'sheet'. Raises ImportRefused for anything else."""
    with path.open("rb") as handle:
        if handle.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
            return "sheet"
    if not backups.integrity_ok(path):
        raise ImportRefused("That database is damaged (it failed SQLite's integrity check).")
    tables = _tables(path)
    if {"django_migrations", "inventory_item"} <= tables:
        return "dispodex"
    if "intake_items" in tables:
        return "legacy"
    raise ImportRefused("That database isn't from Dispodex or the old Pinksheet, so there is nothing to import from it.")


def _check_version(path: Path) -> None:
    """Refuse a database made by a newer Dispodex: this copy wouldn't understand it."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        applied = set(con.execute("SELECT app, name FROM django_migrations").fetchall())
    finally:
        con.close()
    known = set(MigrationLoader(None, ignore_no_migrations=True).disk_migrations)
    if applied - known:
        raise ImportRefused("That database comes from a newer version of Dispodex. Update this copy first, then import it.")


def _item_count(path: Path) -> int:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return con.execute("SELECT COUNT(*) FROM inventory_item").fetchone()[0]
    finally:
        con.close()


def _replace_live_database(source: Path, workdir: Path) -> None:
    """Copy ``source`` over the live database with SQLite's backup API.

    The copy goes through SQLite's own locking, so it is safe while Dispodex is
    running: other connections simply see the new contents on their next query.
    A WAL database only accepts a copy with the same page size, so the file is
    re-packed with the live page size first.
    """
    live = Path(connection.settings_dict["NAME"])
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA page_size")
        page_size = cursor.fetchone()[0]
    connection.close()

    staged = workdir / "staged.sqlite3"
    staged.unlink(missing_ok=True)  # left over when a failed import is being put back
    src =sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        src.execute("VACUUM INTO ?", (str(staged),))
    finally:
        src.close()
    stage = sqlite3.connect(str(staged))
    dest = sqlite3.connect(str(live), timeout=30)
    try:
        stage.execute("PRAGMA journal_mode=DELETE")
        stage.execute(f"PRAGMA page_size={int(page_size)}")
        stage.execute("VACUUM")
        stage.backup(dest)
        dest.execute("PRAGMA journal_mode=WAL")
    finally:
        stage.close()
        dest.close()
    ContentType.objects.clear_cache()


def import_database(path: Path, *, original_name: str, actor: str) -> ImportOutcome:
    """Back up, then import ``path``. Raises ImportRefused when nothing was changed."""
    path = Path(path)
    if not _running.acquire(blocking=False):
        raise ImportRefused("Another import is already running. Wait for it to finish.")
    try:
        kind = detect_kind(path)
        if kind == "dispodex":
            _check_version(path)
        old_export_with_photos = False
        if kind == "sheet":
            old_export_with_photos = (Path(original_name).suffix.lower() in (".xlsx", ".xlsm")
                                      and sheet_import.is_old_app_export(path))
            if old_export_with_photos and Item.all_objects.exists():
                # Merging it would bury every item's notes in the old app's extra columns.
                raise ImportRefused(
                    "This is the old Pinksheet's Excel export. It can only fill an empty Dispodex (it brings the "
                    "photos too). To update a Dispodex that already has items, import a Dispodex backup "
                    "(.sqlite3) or a CSV exported from Dispodex instead.")
            if not old_export_with_photos:
                try:
                    headers, rows = sheet_import.read(path, original_name)
                except sheet_import.SheetError as exc:
                    raise ImportRefused(str(exc)) from exc

        safety = backups.run_backup()
        if not safety.ok or safety.path is None:
            raise ImportRefused(f"The safety backup failed ({safety.error}), so nothing was imported.")

        if old_export_with_photos:
            # A fresh Dispodex and the old app's "Excel with photos" export: bring the photos too.
            try:
                old = excel_import.run(path, replace=False, actor=actor)
            except Exception as exc:
                logger.exception("Old-app Excel import from %s failed", original_name)
                raise ImportRefused(f"The import failed ({exc}). Nothing was changed.") from exc
            items = old.items
            messages = [f"{old.items} items and {old.photos} photos imported from {original_name} "
                        "(photos from this export are small previews, marked low-res)."]
            problems = excel_import.verify(path)
            if problems:
                messages.append(f"{len(problems)} difference(s) found when checking against the file, e.g. {problems[0]}")
        elif kind == "sheet":
            try:
                report = sheet_import.apply(headers, rows, source=original_name)
            except Exception as exc:
                # One transaction: a failure part-way undoes every row by itself.
                logger.exception("Spreadsheet import from %s failed", original_name)
                raise ImportRefused(f"The import failed ({exc}). Nothing was changed.") from exc
            items = report.created + report.updated
            messages = report.lines()
        else:
            items, messages = _import_sqlite(kind, path, original_name, safety.path)

        messages.append(f"The data from before the import is saved as {safety.path.name}.")
        widened = photo_service.widen_small_photos().widened
        if widened:
            messages.append(f"{widened} photo(s) narrower than {photo_service.min_width()}px were widened for eBay.")
        SystemState.set(LAST_IMPORT_KEY, f"{original_name} · {actor} · {timezone.localtime():%b %d, %Y %I:%M %p}")
        logger.info("Import by %s from %s (%s, %s items); previous data backed up as %s",
                    actor, original_name, kind, items, safety.path.name)
        return ImportOutcome(kind=kind, items=items, backup=safety.path.name, messages=messages)
    finally:
        _running.release()


def _import_sqlite(kind: str, path: Path, original_name: str, safety: Path) -> tuple[int, list[str]]:
    """Import a Dispodex or old Pinksheet database; on failure put ``safety`` back."""
    with tempfile.TemporaryDirectory(prefix="dispodex-import-") as tmp:
        workdir = Path(tmp)
        try:
            if kind == "dispodex":
                expected = _item_count(path)
                _replace_live_database(path, workdir)
                call_command("migrate", interactive=False, verbosity=0)
                items = Item.all_objects.count()
                if items != expected:
                    raise RuntimeError(f"expected {expected} items after the import but found {items}")
                return items, [f"{items} items imported from {original_name}.",
                               "Photo files aren't stored in a database: photos added on another computer need copying separately."]
            # The legacy importer reads an old app folder: <root>/data/intake.sqlite.
            (workdir / "data").mkdir()
            shutil.copy2(path, workdir / "data" / "intake.sqlite")
            summary = run_import(workdir, replace=True)
            return summary.items, summary.lines()
        except Exception as exc:
            logger.exception("Database import from %s failed; putting back %s", original_name, safety.name)
            _replace_live_database(safety, workdir)
            raise ImportRefused(f"The import failed ({exc}). Your data was put back as it was.") from exc
