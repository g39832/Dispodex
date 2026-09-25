"""Import legacy CSV exports (from DBeaver, spreadsheets, old systems) into the Archive.

Column names are matched loosely ("Item SKU", "item_sku" and "ITEMSKU" all
work). The full original row is kept in ``legacy_payload``. Rows already
imported (same source, table and legacy id) are skipped, so re-running an
import is safe.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from archive.models import ArchiveItem
from core.skus import normalize_sku

ALIASES = {
    "sku": ["sku", "itemsku", "productsku", "stockcode", "inventorysku"],
    "title": ["title", "whatisit", "itemname", "name", "description", "itemdescription"],
    "status": ["status", "itemstatus", "soldstatus", "legacystatus"],
    "sold_at": ["soldat", "solddate", "datesold"],
    "sold_price": ["soldprice", "saleprice", "price", "finalprice"],
    "purchase_price": ["purchaseprice", "cost", "buyprice", "acquisitioncost"],
    "source": ["source", "originsource", "camefrom", "location"],
    "buyer": ["buyer", "customer", "purchaser", "soldto"],
    "notes": ["notes", "note", "comment", "comments", "memo"],
    "legacy_id": ["legacyid", "recordid", "rowid", "id", "inventoryid"],
    "legacy_location_id": ["locationid", "legacylocationid", "location"],
    "legacy_category_id": ["ebaycategoryid", "categoryid", "legacycategoryid"],
    "created_at": ["createdat", "created", "importedat", "addedat"],
    "updated_at": ["updatedat", "updated", "modifiedat"],
    "legacy_source": ["legacysource"],
    "legacy_table": ["legacytable"],
    "legacy_payload": ["legacypayload"],
}


@dataclass
class ImportReport:
    processed: int = 0
    inserted: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)


def _norm_header(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", header.strip().lower())


def _first(row: dict, keys: list[str]) -> str:
    for key in keys:
        value = (row.get(key) or "").strip()
        if value:
            return value
    return ""


def parse_money(value: str) -> Decimal | None:
    cleaned = re.sub(r"[^0-9.\-]+", "", value or "")
    if not cleaned:
        return None
    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%d-%b-%Y", "%b %d, %Y")


def parse_when(value: str) -> datetime | None:
    """Parse the many date formats found in old exports. Naive values are treated as UTC."""
    text = (value or "").strip()
    if not text:
        return None
    candidates = [text, text.replace("T", " ")]
    # "2025-08-29 11:49:19.749 -0500" (Postgres style)
    match = re.match(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})(\.\d+)?\s*([+-]\d{2}):?(\d{2})$", text)
    if match:
        candidates.insert(0, f"{match.group(1).replace('T', ' ')}{match.group(3)}{match.group(4)}")
    for candidate in candidates:
        for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                moment = datetime.strptime(candidate, fmt)
            except ValueError:
                continue
            return moment if moment.tzinfo else moment.replace(tzinfo=dt_timezone.utc)
        try:
            moment = datetime.fromisoformat(candidate)
            return moment if moment.tzinfo else moment.replace(tzinfo=dt_timezone.utc)
        except ValueError:
            pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=dt_timezone.utc)
        except ValueError:
            continue
    return None


def import_csv(path: Path, source: str = "", table: str = "", dry_run: bool = False) -> ImportReport:
    report = ImportReport()
    default_source = source or path.stem
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        reader = csv.reader(handle)
        try:
            raw_headers = next(reader)
        except StopIteration:
            report.warnings.append("The CSV file is empty.")
            return report
        headers = [_norm_header(h) for h in raw_headers]
        with transaction.atomic():
            for line_number, cells in enumerate(reader, start=2):
                if not cells or all(not c.strip() for c in cells):
                    continue
                report.processed += 1
                if len(cells) != len(headers):
                    report.warnings.append(f"Row {line_number}: {len(cells)} columns, expected {len(headers)}; skipped.")
                    report.skipped += 1
                    continue
                row = {h: (v[:4093] + "...") if len(v) > 4096 else v for h, v in zip(headers, cells) if h}
                values = {name: _first(row, keys) for name, keys in ALIASES.items()}
                payload = values["legacy_payload"] or json.dumps(
                    {h.strip(): c for h, c in zip(raw_headers, cells)}, ensure_ascii=False
                )
                legacy_source = values["legacy_source"] or default_source
                legacy_table = values["legacy_table"] or table
                legacy_id = values["legacy_id"]
                if legacy_id and ArchiveItem.objects.filter(
                    legacy_source=legacy_source, legacy_table=legacy_table, legacy_id=legacy_id
                ).exists():
                    report.skipped += 1
                    continue
                sold = parse_when(values["sold_at"])
                created = parse_when(values["created_at"]) or timezone.now()
                if not dry_run:
                    ArchiveItem.objects.create(
                        created_at=created,
                        updated_at=parse_when(values["updated_at"]),
                        sku=values["sku"][:128],
                        sku_normalized=normalize_sku(values["sku"])[:128],
                        title=values["title"][:1024],
                        status=(values["status"] or "Archived")[:255],
                        sold_at=sold.date() if sold else None,
                        sold_price=parse_money(values["sold_price"]),
                        purchase_price=parse_money(values["purchase_price"]),
                        source=values["source"][:255],
                        buyer=values["buyer"][:255],
                        notes=values["notes"][:4096],
                        legacy_source=legacy_source[:255],
                        legacy_table=legacy_table[:255],
                        legacy_id=legacy_id[:128],
                        legacy_location_id=values["legacy_location_id"][:128],
                        legacy_category_id=values["legacy_category_id"][:128],
                        legacy_payload=payload[:20000],
                    )
                report.inserted += 1
            if dry_run:
                transaction.set_rollback(True)
    return report
