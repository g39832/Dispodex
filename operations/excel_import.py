"""Import items and photos from the old app's "Excel with photos" export.

That export (``export_spreadsheet.php``) writes column letters as ``chr(64 + n)``,
so every column after Z is named ``[``, ``\\``, ``]`` ... which Excel libraries
refuse to open. This module reads the file's XML directly, so the bug doesn't
matter. The workbook is only ever read, never changed.

Photos in that export are small previews (about 320 px). They are stored as-is
and marked ``low_res`` so the app can show a badge and they can be replaced by
the originals or new photos later.

Nothing is lost: a value that doesn't fit its field (an unknown condition, text
longer than the field, an unreadable date, a second price) is written into the
item's notes with its column name.
"""
from __future__ import annotations

import hashlib
import html
import re
import secrets
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.skus import normalize_sku, sku_directory
from inventory.models import (
    LEGACY_CONDITION_MAP,
    LEGACY_STATUS_MAP,
    CompatibleOS,
    Condition,
    Functional,
    IntakeDraft,
    Item,
    ItemEvent,
    ListingImageLayout,
    Photo,
    Review,
    ScriptCache,
    Status,
    YesNo,
    coerce_status,
)

# Export column header -> Item field. Headers not listed here are reported, never silently ignored.
TEXT_COLUMNS = {
    "What is it?": "what_is_it",
    "Brand/Model": "brand_model",
    "Source": "source",
    "eBay Category": "ebay_category",
    "eBay Status": "ebay_status",
    "Where It Goes": "where_it_goes",
    "CPU": "cpu",
    "RAM": "ram",
    "SSD (GB)": "ssd_gb",
    "Graphics Card": "graphics_card",
    "Screen Resolution": "screen_resolution",
    "Battery Health": "battery_health",
    "OS": "os",
    "What Box": "what_box",
    "In eBay Room": "in_ebay_room",
    "eBay Category Path": "ebay_category_path",
    "eBay Category ID": "ebay_category_id",
    "Serial Number": "serial_number",
    "FCC ID": "fcc_id",
    "Notes": "notes",
}
CHOICE_COLUMNS = {
    "Condition": ("condition", Condition),
    "Functional": ("functional", Functional),
    "Power On": ("power_on", YesNo),
    "Compatible OS": ("compatible_os", CompatibleOS),
    "Picture Taken": ("picture_taken", YesNo),
    "Cords/Adapters": ("cords_adapters", YesNo),
    "Keep Items Together": ("keep_items_together", YesNo),
}
FLAG_COLUMNS = {
    "WiFi Card Installed": "wifi_card_installed",
    "Is Square": "is_square",
    "Care if Square": "care_if_square",
    "Diagnostics Ran": "diagnostics_test_ran",
    "Ready": "ready",
}
HANDLED_ELSEWHERE = {"SKU", "Status", "Date Received", "Dispotech Price", "eBay Price", "Qty", "ID", "Created",
                     "Updated", "Photos", "Reviewed"}
# Values that mean "nothing chosen" rather than an answer that should be kept.
BLANK_CHOICES = {"", "none", "n/a", "na", "-", "—"}


class ExcelImportError(Exception):
    """The file can't be imported; the message says why."""


@dataclass
class ExcelRow:
    row: int
    values: dict  # header -> text
    photos: list  # media paths inside the zip, in the order they appear


@dataclass
class ImportReport:
    rows: int = 0
    items: int = 0
    photos: int = 0
    items_with_photos: int = 0
    skipped_no_sku: list = field(default_factory=list)
    duplicates: list = field(default_factory=list)  # (sku, rows kept/dropped)
    kept_in_notes: list = field(default_factory=list)  # (sku, what)
    status_mapped: dict = field(default_factory=dict)  # old text -> lane
    unknown_columns: list = field(default_factory=list)
    missing_columns: list = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [
            f"Rows in the file: {self.rows}",
            f"Items: {self.items}",
            f"Photos: {self.photos} (on {self.items_with_photos} items, marked low-res)",
        ]
        if self.status_mapped:
            out.append("Status spellings mapped: " + ", ".join(f'"{k}" -> {v}' for k, v in sorted(self.status_mapped.items())))
        if self.duplicates:
            out.append(f"Duplicate SKUs (newest row kept): {len(self.duplicates)}")
            out += [f"  - {sku}: kept row {kept}, skipped row {dropped}" for sku, kept, dropped in self.duplicates[:50]]
        if self.skipped_no_sku:
            out.append(f"Rows without a SKU (skipped): {len(self.skipped_no_sku)} -> rows {self.skipped_no_sku[:30]}")
        if self.kept_in_notes:
            out.append(f"Values kept in notes because they don't fit their field: {len(self.kept_in_notes)}")
            out += [f"  - {sku}: {what}" for sku, what in self.kept_in_notes[:60]]
            if len(self.kept_in_notes) > 60:
                out.append(f"  ... and {len(self.kept_in_notes) - 60} more")
        if self.unknown_columns:
            out.append("Columns not recognised (their values were kept in notes): " + ", ".join(self.unknown_columns))
        if self.missing_columns:
            out.append("Not in this export, so not imported: " + ", ".join(self.missing_columns))
        return out


# ── reading the workbook ─────────────────────────────────────────────────────
def _column_index(letters: str) -> int:
    """Real A1 letters (A, Z, AA, ...) or the old exporter's single characters past Z ([, \\, ], ...)."""
    if len(letters) == 1:
        return ord(letters) - 64
    number = 0
    for ch in letters:
        number = number * 26 + (ord(ch.upper()) - 64)
    return number


_CELL = re.compile(r'<c r="([^\d"]+)(\d+)"([^>]*?)(?:/>|>(.*?)</c>)', re.S)


def _cell_text(attrs: str, inner: str | None, shared: list[str]) -> str:
    if not inner:
        return ""
    if 't="s"' in attrs:
        v = re.search(r"<v>(.*?)</v>", inner, re.S)
        return shared[int(v.group(1))] if v else ""
    parts = re.findall(r"<t[^>]*>(.*?)</t>", inner, re.S)
    if parts:
        return html.unescape("".join(parts))
    v = re.search(r"<v>(.*?)</v>", inner, re.S)
    return html.unescape(v.group(1)) if v else ""


def read_workbook(path: Path) -> tuple[list[str], list[ExcelRow], zipfile.ZipFile]:
    try:
        book = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ExcelImportError(f"{path.name} is not an Excel (.xlsx) file: {exc}") from exc
    names = set(book.namelist())
    sheet_name = "xl/worksheets/sheet1.xml"
    if sheet_name not in names:
        raise ExcelImportError("The workbook has no first sheet.")
    shared = []
    if "xl/sharedStrings.xml" in names:
        xml = book.read("xl/sharedStrings.xml").decode("utf-8")
        shared = [html.unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S)))
                  for si in re.findall(r"<si>(.*?)</si>", xml, re.S)]
    sheet = book.read(sheet_name).decode("utf-8")
    cells: dict[int, dict[int, str]] = defaultdict(dict)
    for letters, row, attrs, inner in _CELL.findall(sheet):
        cells[int(row)][_column_index(letters)] = _cell_text(attrs, inner, shared).strip()
    if 1 not in cells:
        raise ExcelImportError("The first sheet has no header row.")
    headers = {col: text for col, text in cells[1].items() if text}
    if "SKU" not in headers.values():
        raise ExcelImportError('The first row has no "SKU" column, so this is not a Pinksheet export.')

    photos = _photos_by_row(book, names)
    rows = []
    for number in sorted(r for r in cells if r > 1):
        values = {headers[col]: text for col, text in cells[number].items() if col in headers}
        if any(values.values()) or photos.get(number):
            rows.append(ExcelRow(row=number, values=values, photos=photos.get(number, [])))
    return [headers[c] for c in sorted(headers)], rows, book


def _photos_by_row(book: zipfile.ZipFile, names: set) -> dict[int, list[str]]:
    """Picture anchors -> sheet row (1-based), in the order they appear (left to right)."""
    drawing, rels_name = "xl/drawings/drawing1.xml", "xl/drawings/_rels/drawing1.xml.rels"
    if drawing not in names or rels_name not in names:
        return {}
    rels = dict(re.findall(r'Id="([^"]+)"[^>]*?Target="([^"]+)"', book.read(rels_name).decode("utf-8")))
    xml = book.read(drawing).decode("utf-8")
    found: dict[int, list[tuple[int, int, str]]] = defaultdict(list)
    for anchor in re.findall(r"<xdr:(?:oneCellAnchor|twoCellAnchor|absoluteAnchor)\b.*?</xdr:(?:oneCellAnchor|twoCellAnchor|absoluteAnchor)>", xml, re.S):
        frm = re.search(r"<xdr:from>.*?<xdr:col>(\d+)</xdr:col>\s*<xdr:colOff>(-?\d+)</xdr:colOff>\s*<xdr:row>(\d+)</xdr:row>", anchor, re.S)
        embed = re.search(r'r:embed="([^"]+)"', anchor)
        if not frm or not embed or embed.group(1) not in rels:
            continue
        target = rels[embed.group(1)]
        media = "xl/" + target.replace("../", "") if not target.startswith("/") else target.lstrip("/")
        col, col_off, row = int(frm.group(1)), int(frm.group(2)), int(frm.group(3)) + 1
        found[row].append((col, col_off, media))
    return {row: [m for _, _, m in sorted(items, key=lambda t: (t[0], t[1]))] for row, items in found.items()}


# ── turning a row into an item ───────────────────────────────────────────────
def _when(text: str) -> datetime | None:
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:  # Excel date serial number
            moment = datetime(1899, 12, 30) + __import__("datetime").timedelta(days=float(text))
        except ValueError:
            return None
    return moment if moment.tzinfo else moment.replace(tzinfo=dt_timezone.utc)  # the old app stored UTC


def _date(text: str) -> date | None:
    moment = _when(text)
    return moment.date() if moment else None


def _money(text: str) -> Decimal | None:
    cleaned = (text or "").replace("$", "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        value = Decimal(cleaned).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    return value if value >= 0 else None


def _flag(text: str) -> bool:
    return (text or "").strip().lower() in {"1", "true", "yes", "y", "on", "x"}


def build_item(row: ExcelRow, report: ImportReport) -> Item | None:
    v = row.values
    sku = (v.get("SKU") or "").strip().upper()
    sku_norm = normalize_sku(sku)
    if not sku_norm:
        report.skipped_no_sku.append(row.row)
        return None
    extra_notes: list[str] = []

    def keep(label: str, value: str, why: str = "") -> None:
        extra_notes.append(f"{label}: {value}")
        report.kept_in_notes.append((sku_norm, f"{label} = {value[:60]}{'…' if len(value) > 60 else ''}{' (' + why + ')' if why else ''}"))

    item = Item(sku=sku[:64], sku_normalized=sku_norm[:64])
    raw_status = (v.get("Status") or "").strip()
    item.status = coerce_status(raw_status)
    if raw_status and raw_status.lower() != item.status:
        report.status_mapped[raw_status] = Status(item.status).label
        if raw_status.lower() not in Status.values and raw_status.lower() not in LEGACY_STATUS_MAP:
            keep("Status", raw_status, "not a known stage; placed in Intake, as the old board did")

    for header, name in TEXT_COLUMNS.items():
        text = v.get(header, "")
        if not text:
            continue
        limit = Item._meta.get_field(name).max_length
        if limit and len(text) > limit:
            setattr(item, name, text[:limit])
            keep(f"{header} (full text)", text, f"longer than {limit} characters")
        else:
            setattr(item, name, text)

    for header, (name, choices) in CHOICE_COLUMNS.items():
        text = (v.get(header) or "").strip()
        if text.lower() in BLANK_CHOICES:
            continue
        match = next((c for c in choices.values if c.lower() == text.lower()), "")
        if not match and name == "condition":
            match = LEGACY_CONDITION_MAP.get(text.lower(), "")
        if match:
            setattr(item, name, match)
        else:
            keep(header, text, "not one of the app's choices")

    for header, name in FLAG_COLUMNS.items():
        if header in v:
            setattr(item, name, _flag(v[header]))

    raw_date = v.get("Date Received", "")
    item.date_received = _date(raw_date)
    if raw_date and item.date_received is None:
        keep("Date received", raw_date, "not a date")

    dispo, ebay = _money(v.get("Dispotech Price", "")), _money(v.get("eBay Price", ""))
    item.price = dispo if dispo is not None else ebay
    if dispo is not None and ebay is not None and dispo != ebay:
        keep("eBay price", f"${ebay:,.2f}", f"differs from the store price ${dispo:,.2f}, which was used")
    for header in ("Dispotech Price", "eBay Price"):
        if v.get(header) and _money(v[header]) is None:
            keep(header, v[header], "not a number")

    qty_text = (v.get("Qty") or "").strip()
    try:
        item.quantity = max(1, int(float(qty_text))) if qty_text else 1
    except ValueError:
        item.quantity = 1
        keep("Qty", qty_text, "not a number")

    item.reviewed = Review.SOLD if item.status == Status.SOLD else Review.INACTIVE
    created = _when(v.get("Created", "")) or timezone.now()
    item.created_at = created
    item.updated_at = _when(v.get("Updated", "")) or created

    for header, text in v.items():
        if text and header not in TEXT_COLUMNS and header not in CHOICE_COLUMNS and header not in FLAG_COLUMNS \
                and header not in HANDLED_ELSEWHERE:
            keep(header, text, "column not recognised")

    if extra_notes:
        block = "Imported from the old app (values that didn't fit a field):\n" + "\n".join(f"- {n}" for n in extra_notes)
        item.notes = (item.notes + "\n\n" + block).strip() if item.notes else block
    return item


# ── the import ───────────────────────────────────────────────────────────────
def plan(path: Path) -> tuple[ImportReport, list[tuple[Item, ExcelRow]], zipfile.ZipFile]:
    """Read and check everything without writing anything (the preview)."""
    headers, rows, book = read_workbook(Path(path))
    report = ImportReport(rows=len(rows))
    known = set(TEXT_COLUMNS) | set(CHOICE_COLUMNS) | set(FLAG_COLUMNS) | HANDLED_ELSEWHERE
    report.unknown_columns = [h for h in headers if h not in known]
    report.missing_columns = [h for h in ("Notes", "Serial Number", "Ready", "Reviewed") if h not in headers]

    newest: dict[str, tuple[Item, ExcelRow]] = {}
    for row in rows:
        item = build_item(row, report)
        if item is None:
            continue
        current = newest.get(item.sku_normalized)
        if current is None:
            newest[item.sku_normalized] = (item, row)
            continue
        keep_new = (item.updated_at, row.row) > (current[0].updated_at, current[1].row)
        kept, dropped = ((item, row), current) if keep_new else (current, (item, row))
        newest[item.sku_normalized] = kept
        report.duplicates.append((item.sku_normalized, kept[1].row, dropped[1].row))
    planned = list(newest.values())
    report.items = len(planned)
    report.photos = sum(len(r.photos) for _, r in planned)
    report.items_with_photos = sum(1 for _, r in planned if r.photos)
    return report, planned, book


def run(path: Path, *, replace: bool, actor: str = "Import (Excel export)") -> ImportReport:
    """Import for real. With ``replace`` the current items, photos and drafts are removed first
    (the Archive is kept). The caller is responsible for the backup."""
    report, planned, book = plan(path)
    if Item.all_objects.exists() and not replace:
        raise ExcelImportError("Dispodex already has items. Use --replace to swap them for the file's items.")

    root = Path(settings.MEDIA_ROOT)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if replace:
        # Move old photo files aside instead of deleting them (they stay recoverable).
        for folder in ("sku_photos", "thumbs"):
            if (root / folder).exists() and any((root / folder).iterdir()):
                (root / folder).rename(root / f"{folder}-before-import-{stamp}")

    written: list[Path] = []
    try:
        with transaction.atomic():
            if replace:
                for model in (Photo, IntakeDraft, ScriptCache, ListingImageLayout, ItemEvent):
                    model.objects.all().delete()
                from squaresync.models import CatalogSync, Sale, SyncJob
                for model in (SyncJob, CatalogSync, Sale):
                    model.objects.all().delete()
                Item.all_objects.all().delete()

            items = [item for item, _ in planned]
            Item.all_objects.bulk_create(items, batch_size=500)
            by_sku = {i.sku_normalized: i for i in Item.all_objects.filter(sku_normalized__in=[i.sku_normalized for i in items])}

            photos = []
            for item, row in planned:
                for position, media in enumerate(row.photos, start=1):
                    data = book.read(media)
                    ext = Path(media).suffix.lower() or ".jpg"
                    stored = f"{secrets.token_hex(16)}{ext}"
                    target = root / "sku_photos" / sku_directory(item.sku_normalized) / stored
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    written.append(target)
                    photos.append(Photo(
                        sku_normalized=item.sku_normalized,
                        original_name=f"{item.sku_normalized} photo {position} (from export){ext}",
                        stored_name=stored,
                        mime_type="image/png" if ext == ".png" else "image/jpeg",
                        file_size=len(data),
                        is_thumb=position == 1,
                        sort_order=position,
                        created_at=item.created_at,
                        low_res=True,
                    ))
            Photo.objects.bulk_create(photos, batch_size=500)

            ItemEvent.objects.bulk_create([
                ItemEvent(item=by_sku[item.sku_normalized], sku_normalized=item.sku_normalized,
                          action=ItemEvent.Action.CREATED, actor=actor[:64],
                          note=f"Imported from {Path(path).name}" + (f" with {len(row.photos)} low-res photo(s)" if row.photos else ""),
                          created_at=timezone.now())
                for item, row in planned
            ], batch_size=500)
    except Exception:
        for target in written:  # the database rolled back; don't leave stray files
            target.unlink(missing_ok=True)
        raise
    return report


def verify(path: Path) -> list[str]:
    """Compare what's in Dispodex with the file, item by item and photo by photo.
    Returns a list of problems (empty = everything matches)."""
    report, planned, book = plan(path)
    problems: list[str] = []
    root = Path(settings.MEDIA_ROOT)
    for expected, row in planned:
        item = Item.objects.filter(sku_normalized=expected.sku_normalized).first()
        if item is None:
            problems.append(f"{expected.sku_normalized}: missing")
            continue
        for name in ["status", "what_is_it", "brand_model", "source", "date_received", "condition", "functional",
                     "power_on", "price", "quantity", "ebay_category", "ebay_status", "where_it_goes", "cpu", "ram",
                     "ssd_gb", "graphics_card", "screen_resolution", "battery_health", "os", "compatible_os",
                     "wifi_card_installed", "picture_taken", "cords_adapters", "keep_items_together", "what_box",
                     "in_ebay_room", "is_square", "care_if_square", "diagnostics_test_ran", "ebay_category_path",
                     "ebay_category_id", "notes", "created_at", "updated_at"]:
            if getattr(item, name) != getattr(expected, name):
                problems.append(f"{item.sku_normalized}: {name} is {getattr(item, name)!r}, file says {getattr(expected, name)!r}")
        stored = list(Photo.objects.filter(sku_normalized=item.sku_normalized).order_by("sort_order", "id"))
        if len(stored) != len(row.photos):
            problems.append(f"{item.sku_normalized}: {len(stored)} photos, file has {len(row.photos)}")
            continue
        for photo, media in zip(stored, row.photos):
            path_on_disk = root / "sku_photos" / sku_directory(photo.sku_normalized) / photo.stored_name
            if not path_on_disk.exists():
                problems.append(f"{item.sku_normalized}: photo file {photo.stored_name} missing")
            elif hashlib.sha256(path_on_disk.read_bytes()).digest() != hashlib.sha256(book.read(media)).digest():
                problems.append(f"{item.sku_normalized}: photo {photo.sort_order} differs from the file")
    extra = Item.objects.exclude(sku_normalized__in=[i.sku_normalized for i, _ in planned]).count()
    if extra:
        problems.append(f"{extra} item(s) in Dispodex that are not in the file")
    return problems
