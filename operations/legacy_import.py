"""Copy everything out of the old PHP Pinksheet into this app.

Reads the old SQLite files (never writes to them) and copies photo and
listing-image folders. The PHP app is left completely untouched.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from archive.models import ArchiveItem
from core.skus import normalize_sku, sku_directory
from inventory.models import (
    CompatibleOS,
    Condition,
    Functional,
    IntakeDraft,
    LEGACY_CONDITION_MAP,
    Item,
    ListingImageLayout,
    Photo,
    Review,
    ScriptCache,
    YesNo,
    coerce_status,
)
from squaresync.models import CatalogSync, Sale


@dataclass
class ImportSummary:
    items: int = 0
    deleted_items: int = 0
    duplicates_skipped: int = 0
    photos: int = 0
    photo_files_missing: int = 0
    drafts: int = 0
    scripts: int = 0
    archive: int = 0
    catalog_mappings: int = 0
    sales: int = 0
    layouts: int = 0
    listing_images: int = 0
    notes: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        return [
            f"Items imported: {self.items} (plus {self.deleted_items} deleted items kept for undo)",
            f"Duplicate SKU rows merged: {self.duplicates_skipped}",
            f"Photos: {self.photos} ({self.photo_files_missing} listed in the old database but missing on disk)",
            f"Unsaved intake drafts: {self.drafts}",
            f"eBay scripts: {self.scripts}",
            f"Archive records: {self.archive}",
            f"Square catalog mappings: {self.catalog_mappings}",
            f"Square sales: {self.sales}",
            f"Listing-image layouts: {self.layouts} ({self.listing_images} image files)",
            *self.notes,
        ]


# ── value cleaning ───────────────────────────────────────────────────────────
def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _when(value) -> datetime | None:
    """Old timestamps were UTC 'YYYY-MM-DD HH:MM:SS' (SQLite datetime('now')) or ISO with offset."""
    text = _text(value)
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt_timezone.utc)
    return moment


def _date(value) -> date | None:
    text = _text(value)[:10]
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def _money(value) -> Decimal | None:
    if value is None or _text(value) == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _choice(value, choices) -> str:
    text = _text(value)
    for option in choices.values:
        if text.lower() == option.lower():
            return option
    return ""


def _flag(value) -> bool:
    return _text(value).lower() in {"1", "true", "yes", "on"}


def _int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _rows(con: sqlite3.Connection, table: str) -> list[dict]:
    exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if not exists:
        return []
    con.row_factory = sqlite3.Row
    return [dict(row) for row in con.execute(f'SELECT * FROM "{table}"')]


def _item_from_row(row: dict) -> Item:
    price = _money(row.get("dispotech_price"))
    if price is None:
        price = _money(row.get("ebay_price"))
    status = coerce_status(row.get("status"))
    reviewed = _int(row.get("reviewed"))
    notes = _text(row.get("notes"))
    raw_date = _text(row.get("date_received"))
    received = _date(raw_date)
    if raw_date and received is None:
        notes = (notes + f"\nDate received (from old sheet): {raw_date}").strip()
    created = _when(row.get("created_at")) or timezone.now()
    sku = _text(row.get("sku")).upper()
    return Item(
        sku=sku,
        sku_normalized=normalize_sku(row.get("sku_normalized") or sku),
        status=status,
        what_is_it=_text(row.get("what_is_it"))[:255],
        ebay_category=_text(row.get("ebay_category"))[:255],
        ebay_category_path=_text(row.get("ebay_category_path"))[:512],
        ebay_category_id=_text(row.get("ebay_category_id"))[:32],
        date_received=received,
        source=_text(row.get("source"))[:255],
        where_it_goes=_text(row.get("where_it_goes"))[:255],
        functional=_choice(row.get("functional"), Functional),
        condition=_choice(row.get("condition"), Condition)
        or LEGACY_CONDITION_MAP.get(_text(row.get("condition")).lower(), ""),
        is_square=_flag(row.get("is_square")),
        care_if_square=_flag(row.get("care_if_square")),
        cords_adapters=_choice(row.get("cords_adapters"), YesNo),
        keep_items_together=_choice(row.get("keep_items_together"), YesNo),
        picture_taken=_choice(row.get("picture_taken"), YesNo),
        power_on=_choice(row.get("power_on"), YesNo),
        brand_model=_text(row.get("brand_model"))[:255],
        ram=_text(row.get("ram"))[:255],
        ssd_gb=_text(row.get("ssd_gb"))[:255],
        cpu=_text(row.get("cpu"))[:255],
        os=_text(row.get("os"))[:255],
        compatible_os=_choice(row.get("compatible_os"), CompatibleOS),
        battery_health=_text(row.get("battery_health"))[:255],
        graphics_card=_text(row.get("graphics_card"))[:255],
        screen_resolution=_text(row.get("screen_resolution"))[:255],
        diagnostics_test_ran=_flag(row.get("diagnostics_test_ran")),
        wifi_card_installed=_flag(row.get("wifi_card_installed")),
        serial_number=_text(row.get("serial_number"))[:128],
        ebay_status=_text(row.get("ebay_status"))[:255],
        in_ebay_room=_text(row.get("in_ebay_room"))[:255],
        what_box=_text(row.get("what_box"))[:255],
        price=price,
        quantity=max(1, _int(row.get("quantity"), 1)),
        notes=notes,
        reviewed=Review.SOLD if status == "sold" else (reviewed if reviewed in (0, 1, 2) else 0),
        ready=_flag(row.get("ready")),
        created_at=created,
        updated_at=_when(row.get("updated_at")) or created,
    )


def _copy_tree_files(source: Path, dest: Path) -> int:
    copied = 0
    if not source.exists():
        return 0
    for path in source.rglob("*"):
        if path.is_file():
            target = dest / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(path, target)
            copied += 1
    return copied


def run_import(source_root: Path, replace: bool = False) -> ImportSummary:
    source_root = Path(source_root)
    data_dir = source_root / "data"
    intake_db = data_dir / "intake.sqlite"
    archive_db = data_dir / "archive.sqlite"
    if not intake_db.exists():
        raise FileNotFoundError(f"Could not find the old database at {intake_db}")

    if Item.all_objects.exists() and not replace:
        raise RuntimeError(
            "This Dispodex already has items. Run with --replace to wipe them and import again."
        )
    summary = ImportSummary()
    # Read-only connections: the PHP app's files are never modified.
    con = sqlite3.connect(f"file:{intake_db}?mode=ro", uri=True)
    arch = sqlite3.connect(f"file:{archive_db}?mode=ro", uri=True) if archive_db.exists() else None
    try:
        items_rows = _rows(con, "intake_items")
        deleted_rows = _rows(con, "intake_deleted")
        photo_rows = _rows(con, "sku_photos")
        draft_rows = _rows(con, "intake_drafts")
        script_rows = _rows(con, "script_cache")
        mapping_rows = _rows(con, "square_catalog_sync")
        sale_rows = _rows(con, "sales_history")
        archive_rows = _rows(arch, "archive_items") if arch else []
        if not archive_rows:
            archive_rows = _rows(con, "archive_items")
    finally:
        con.close()
        if arch:
            arch.close()

    with transaction.atomic():
        if replace:
            for model in (Photo, IntakeDraft, ScriptCache, ListingImageLayout, ArchiveItem, CatalogSync, Sale):
                model.objects.all().delete()
            Item.all_objects.all().delete()

        # Items: one live record per SKU (newest wins, like the PHP app's own clean-up).
        items_rows.sort(key=lambda r: (_text(r.get("updated_at")), _int(r.get("id"))), reverse=True)
        seen: set[str] = set()
        live = []
        for row in items_rows:
            item = _item_from_row(row)
            if item.sku_normalized and item.sku_normalized in seen:
                summary.duplicates_skipped += 1
                continue
            if item.sku_normalized:
                seen.add(item.sku_normalized)
            live.append(item)
        Item.all_objects.bulk_create(live, batch_size=500)
        summary.items = len(live)

        deleted = []
        for row in deleted_rows:
            item = _item_from_row(row)
            item.deleted_at = _when(row.get("deleted_at")) or timezone.now()
            deleted.append(item)
        Item.all_objects.bulk_create(deleted, batch_size=500)
        summary.deleted_items = len(deleted)

        # Photos: metadata + files (same folder layout as before).
        old_photos = data_dir / "sku_photos"
        new_photos = Path(settings.MEDIA_ROOT) / "sku_photos"
        photos = []
        for row in sorted(photo_rows, key=lambda r: _int(r.get("id"))):
            sku = normalize_sku(row.get("sku_normalized"))
            stored = Path(_text(row.get("stored_name"))).name
            if not sku or not stored:
                continue
            source = old_photos / sku_directory(sku) / stored
            if not source.exists():
                summary.photo_files_missing += 1
                continue
            target = new_photos / sku_directory(sku) / stored
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(source, target)
            photos.append(
                Photo(
                    id=_int(row.get("id")) or None,  # keep ids so old photo links keep working
                    sku_normalized=sku,
                    original_name=_text(row.get("original_name"))[:255] or stored,
                    stored_name=stored,
                    mime_type=_text(row.get("mime_type")) or "image/png",
                    file_size=_int(row.get("file_size")) or target.stat().st_size,
                    is_thumb=_flag(row.get("is_thumb")),
                    sort_order=_int(row.get("sort_order")),
                    created_at=_when(row.get("created_at")) or timezone.now(),
                )
            )
        Photo.objects.bulk_create(photos, batch_size=500)
        summary.photos = len(photos)

        for row in draft_rows:
            sku = normalize_sku(row.get("sku_normalized"))
            try:
                payload = json.loads(row.get("payload") or "{}")
            except ValueError:
                continue
            if not sku or not isinstance(payload, dict):
                continue
            IntakeDraft.objects.update_or_create(
                sku_normalized=sku,
                defaults={"payload": payload, "version": max(1, _int(row.get("version"), 1)),
                          "updated_at": _when(row.get("updated_at")) or timezone.now()},
            )
            summary.drafts += 1

        for row in script_rows:
            sku = normalize_sku(row.get("sku_normalized"))
            if not sku:
                continue
            ScriptCache.objects.update_or_create(
                sku_normalized=sku,
                defaults={
                    "sku_display": _text(row.get("sku_display"))[:64] or sku,
                    "prompt_text": _text(row.get("prompt_text")),
                    "chatgpt_text": _text(row.get("chatgpt_text")),
                    "final_text": _text(row.get("final_text")),
                    "updated_at": _when(row.get("updated_at")) or timezone.now(),
                },
            )
            summary.scripts += 1

        archive = []
        for row in archive_rows:
            sold = _when(row.get("sold_at"))
            archive.append(
                ArchiveItem(
                    created_at=_when(row.get("created_at")) or timezone.now(),
                    updated_at=_when(row.get("updated_at")),
                    sku=_text(row.get("sku"))[:128],
                    sku_normalized=normalize_sku(row.get("sku_normalized") or row.get("sku"))[:128],
                    title=_text(row.get("title"))[:1024],
                    status=_text(row.get("status"))[:255],
                    sold_at=sold.date() if sold else _date(row.get("sold_at")),
                    sold_price=_money(row.get("sold_price")),
                    purchase_price=_money(row.get("purchase_price")),
                    source=_text(row.get("source"))[:255],
                    buyer=_text(row.get("buyer"))[:255],
                    notes=_text(row.get("notes")),
                    legacy_source=_text(row.get("legacy_source"))[:255],
                    legacy_table=_text(row.get("legacy_table"))[:255],
                    legacy_id=_text(row.get("legacy_id"))[:128],
                    legacy_location_id=_text(row.get("legacy_location_id"))[:128],
                    legacy_category_id=_text(row.get("legacy_category_id"))[:128],
                    legacy_payload=_text(row.get("legacy_payload")),
                )
            )
        ArchiveItem.objects.bulk_create(archive, batch_size=500, ignore_conflicts=True)
        summary.archive = ArchiveItem.objects.count()

        for row in mapping_rows:
            sku = normalize_sku(row.get("sku_normalized"))
            if not sku or not _text(row.get("square_item_id")):
                continue  # rows without a Square item are just old errors; the queue will redo them
            CatalogSync.objects.update_or_create(
                sku_normalized=sku,
                defaults={
                    "square_item_id": _text(row.get("square_item_id")),
                    "square_item_version": _int(row.get("square_item_version")) or None,
                    "square_variation_id": _text(row.get("square_variation_id")),
                    "square_variation_version": _int(row.get("square_variation_version")) or None,
                    "square_image_id": _text(row.get("square_image_id")),
                    "last_synced_at": _when(row.get("last_synced_at")),
                    "previous_status": coerce_status(row.get("previous_status")) if row.get("previous_status") else "",
                },
            )
            summary.catalog_mappings += 1

        for row in sale_rows:
            sku = normalize_sku(row.get("sku_normalized"))
            if not sku:
                continue
            Sale.objects.get_or_create(
                square_order_id=_text(row.get("square_order_id")),
                sku_normalized=sku,
                defaults={
                    "sku": _text(row.get("sku")) or sku,
                    "square_payment_id": _text(row.get("square_payment_id")),
                    "sale_price": _money(row.get("sale_price")) or Decimal("0"),
                    "tax_amount": _money(row.get("tax_amount")) or Decimal("0"),
                    "discount_amount": _money(row.get("discount_amount")) or Decimal("0"),
                    "line_item_quantity": max(1, _int(row.get("line_item_quantity"), 1)),
                    "sold_at": _when(row.get("sold_at")) or timezone.now(),
                    "location_id": _text(row.get("location_id")),
                    "receipt_number": _text(row.get("receipt_number"))[:64],
                },
            )
            summary.sales += 1

        layouts_dir = data_dir / "layouts"
        if layouts_dir.exists():
            for path in layouts_dir.glob("*.json"):
                try:
                    positions = json.loads(path.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    continue
                if isinstance(positions, list):
                    for entry in positions:
                        src = str(entry.get("src") or "") if isinstance(entry, dict) else ""
                        if src.startswith("ebay_serve.php"):
                            params = dict(p.split("=", 1) for p in src.split("?", 1)[-1].split("&") if "=" in p)
                            entry["src"] = f"/listing-images/files/{params.get('sku', '')}/{params.get('file', '')}"
                        elif src.startswith("photo.php?id="):
                            entry["src"] = "/photos/" + src.split("=", 1)[1].split("&")[0] + "/"
                    ListingImageLayout.objects.update_or_create(
                        sku_normalized=normalize_sku(path.stem), defaults={"positions": positions}
                    )
                    summary.layouts += 1

    summary.listing_images = _copy_tree_files(data_dir / "ebay_images", Path(settings.MEDIA_ROOT) / "ebay_images")
    snapshot = data_dir / "ebay_categories.json"
    if snapshot.exists():
        shutil.copy2(snapshot, Path(settings.DATA_DIR) / "ebay_categories.json")
        summary.notes.append("Copied the downloaded eBay category list.")
    return summary
