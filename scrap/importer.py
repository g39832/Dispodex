"""Import a scrap log from a spreadsheet (a Google Sheet downloaded as CSV, or Excel).

Columns are matched loosely ("Weight (lbs)", "Lbs", "Pounds" all mean the
weight) and the person can change the matching on the preview before anything
is saved. Each row becomes one scrap entry. Every imported row keeps a
fingerprint of its values, so importing the same sheet again (say, after more
rows were added to it) only brings in the new rows.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from operations.sheet_import import SheetError, _norm, _parse_date, read_table
from scrap.models import ScrapCategory, ScrapEntry

MAX_ROWS = 20000
MAX_WEIGHT = Decimal("100000")
KG_TO_LBS = Decimal("2.20462")

# Field -> (label shown on the preview, loose column names it matches).
FIELDS: dict[str, tuple[str, list[str]]] = {
    "weight": ("Weight (lbs)", ["Weight", "Weight (lbs)", "Weight lbs", "Lbs", "Lb", "Pounds", "Net weight",
                                "Total weight", "Wt", "Scrap weight", "Weight (lb)"]),
    "date": ("Date taken in", ["Date", "Timestamp", "Date taken in", "Taken in", "Date in", "Date received",
                               "Received", "Date weighed", "Weighed", "Weighed on", "Day", "When", "Time"]),
    "description": ("What it is", ["What is it", "What", "Item", "Items", "Item name", "Description", "Product",
                                   "Equipment", "Device"]),
    "drop_off": ("Drop-off (public or company)", ["Drop off", "Drop-off", "Dropoff", "Drop off type", "Public or company",
                                                  "Source", "From", "Customer type", "Customer"]),
    "company": ("Company name", ["Company", "Company name", "Business", "Business name"]),
    "category": ("Category", ["Category", "Type", "Kind", "Material", "Scrap type", "Scrap category"]),
    "weighed_by": ("Taken in by", ["Taken in by", "Received by", "Weighed by", "Name", "Person", "Employee", "Who", "By", "Worker", "Tech",
                                  "Your name", "Email address", "Email"]),
    "moved": ("Moved to scrap", ["Moved", "Moved to scrap", "Date moved", "Moved on", "Moved date", "Status",
                                 "Scrapped", "Done"]),
    "moved_by": ("Moved by", ["Moved by", "Taken by", "Carried by"]),
    "note": ("Note", ["Note", "Notes", "Comment", "Comments", "Memo"]),
}
# The fingerprint that spots already-imported rows uses these columns only. Keep this list as it is:
# changing it would make rows imported before look new and import them twice.
KEY_FIELDS = ("weight", "date", "description", "category", "weighed_by", "moved", "moved_by", "note")
_ALIASES = {_norm(alias): name for name, (_, aliases) in FIELDS.items() for alias in aliases}

YES_WORDS = {"yes", "y", "true", "x", "1", "moved", "done", "scrapped", "scrap", "complete", "completed"}
NO_WORDS = {"no", "n", "false", "0", "waiting", "not moved", "pending", "open"}


SUFFIXES = {".csv", ".tsv", ".txt", ".xlsx", ".xlsm"}


def read(path: Path, original_name: str) -> tuple[list[str], list[dict]]:
    if Path(original_name).suffix.lower() not in SUFFIXES:
        raise SheetError("Pick a CSV file (in Google Sheets: File → Download → Comma-separated values) "
                         "or an Excel .xlsx file.")
    headers, rows = read_table(path, original_name)
    if not headers:
        raise SheetError("That file has no column names in its first row.")
    if not rows:
        raise SheetError("The file has column names but no rows.")
    if len(rows) > MAX_ROWS:
        raise SheetError(f"That file has {len(rows):,} rows. Import at most {MAX_ROWS:,} at a time.")
    return headers, rows


def guess_mapping(headers: list[str]) -> dict[str, str]:
    """{field: header} for every field a column name clearly matches. Each column is used once."""
    mapping: dict[str, str] = {}
    for header in headers:
        name = _ALIASES.get(_norm(header))
        if name and name not in mapping:
            mapping[name] = header
    if "weight" not in mapping:
        # "Weight of item (lbs)" and the like: any column mentioning weight or pounds.
        for header in headers:
            if header not in mapping.values() and re.search(r"weight|lbs?\b|pounds", header, re.I):
                mapping["weight"] = header
                break
    return mapping


# ── turning cells into values ────────────────────────────────────────────────
def parse_weight(text: str) -> Decimal | None:
    """Pounds from "12.5", "12.5 lbs", "1,200 lb" or "4 kg". None if it isn't a usable weight."""
    value = str(text or "").strip().lower()
    match = re.search(r"\d[\d,]*(?:\.\d+)?|\.\d+", value)
    if not match:
        return None
    try:
        number = Decimal(match.group().replace(",", ""))
    except InvalidOperation:
        return None
    if re.search(r"\bkgs?\b|kilo", value):
        number *= KG_TO_LBS
    elif re.search(r"\boz\b|ounce", value):
        number /= 16
    number = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if number <= 0 or number >= MAX_WEIGHT:
        return None
    return number


_DATETIME_FORMATS = (
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%m/%d/%y %H:%M", "%m/%d/%y %I:%M %p",
)


def parse_when(text: str) -> datetime | None:
    """A date, or date and time, in the shop's time zone. A date alone counts as noon that day."""
    value = " ".join(str(text or "").split())
    if not value:
        return None
    for fmt in _DATETIME_FORMATS:
        try:
            moment = datetime.strptime(value, fmt)
            break
        except ValueError:
            continue
    else:
        day = _parse_date(value)
        if day is None:
            return None
        moment = datetime.combine(day, time(12, 0))
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment)
    return moment


@dataclass
class Row:
    line: int
    weight: Decimal
    weighed_at: datetime
    description: str
    category: str
    weighed_by: str
    moved_at: datetime | None
    moved_by: str
    note: str
    key: str
    drop_off: str = ""
    company: str = ""
    duplicate: bool = False


@dataclass
class Plan:
    """What an import would do, shown on the preview and then saved as-is."""

    rows: list[Row] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    total_rows: int = 0

    @property
    def new_rows(self) -> list[Row]:
        return [row for row in self.rows if not row.duplicate]

    @property
    def duplicates(self) -> int:
        return sum(row.duplicate for row in self.rows)

    @property
    def new_lbs(self) -> Decimal:
        return sum((row.weight for row in self.new_rows), Decimal("0"))

    @property
    def new_moved(self) -> int:
        return sum(row.moved_at is not None for row in self.new_rows)

    @property
    def new_waiting(self) -> int:
        return len(self.new_rows) - self.new_moved

    @property
    def new_categories(self) -> list[str]:
        names = {row.category.lower(): row.category for row in self.new_rows if row.category}
        existing = {name.lower() for name in ScrapCategory.objects.values_list("name", flat=True)}
        return sorted((name for key, name in names.items() if key not in existing), key=str.lower)


def _clip(text: str, length: int) -> str:
    return " ".join(str(text or "").split())[:length]


def plan(rows: list[dict], mapping: dict[str, str], *, moved_default: bool, importer: str) -> Plan:
    """Work out every row without saving anything. Rows without a usable weight are skipped with a reason."""
    if not mapping.get("weight"):
        raise SheetError("Pick which column has the weight.")
    result = Plan(total_rows=len(rows))
    seen: dict[str, int] = {}
    now = timezone.now()
    for line, raw in enumerate(rows, start=2):  # line 1 is the column names
        cell = {name: str(raw.get(header, "") or "").strip() for name, header in mapping.items() if header}
        weight = parse_weight(cell.get("weight", ""))
        if weight is None:
            shown = cell.get("weight") or "blank"
            result.skipped.append(f"row {line}: weight “{shown[:20]}” isn't a number of pounds")
            continue
        weighed_at = parse_when(cell.get("date", ""))
        if cell.get("date") and weighed_at is None:
            result.skipped.append(f"row {line}: date “{cell['date'][:24]}” wasn't understood")
            continue
        weighed_at = weighed_at or now
        moved_at = None
        moved_text = cell.get("moved", "")
        if "moved" in mapping and mapping["moved"]:
            moved_when = parse_when(moved_text)
            if moved_when is not None:
                moved_at = moved_when
            elif moved_text.lower() in YES_WORDS:
                moved_at = weighed_at
            elif moved_text and moved_text.lower() not in NO_WORDS:
                moved_at = weighed_at  # any other note in the column ("on pallet 3") still means it went over
        elif moved_default:
            moved_at = weighed_at
        # Identical rows in one sheet are separate items: number them so each keeps its own fingerprint.
        values = "|".join(cell.get(name, "").lower() for name in KEY_FIELDS)
        seen[values] = seen.get(values, 0) + 1
        key = hashlib.sha256(f"{values}|{seen[values]}".encode()).hexdigest()
        weighed_by = _clip(cell.get("weighed_by", ""), 80) or importer
        result.rows.append(Row(
            line=line, weight=weight, weighed_at=weighed_at, description=_clip(cell.get("description", ""), 200),
            category=_clip(cell.get("category", ""), 40), weighed_by=weighed_by, moved_at=moved_at,
            moved_by=(_clip(cell.get("moved_by", ""), 80) or weighed_by) if moved_at else "",
            note=_clip(cell.get("note", ""), 255), key=key,
            drop_off=drop_off_from(cell.get("drop_off", "")), company=_clip(cell.get("company", ""), 120),
        ))
    known = set(ScrapEntry.objects.filter(import_key__in=[row.key for row in result.rows])
                .values_list("import_key", flat=True))
    for row in result.rows:
        row.duplicate = row.key in known
    return result


def drop_off_from(text: str) -> str:
    """"Public", "public drop-off", "Company", "Business" … -> the stored choice ("" if it doesn't say)."""
    value = str(text or "").strip().lower()
    if not value:
        return ""
    if re.search(r"compan|business|corp|commercial|\bb2b\b|\bllc\b|\binc\b", value):
        return ScrapEntry.DropOff.COMPANY
    if re.search(r"public|resident|walk|person|individual|customer", value):
        return ScrapEntry.DropOff.PUBLIC
    return ""


def category_for(name: str, created_by: str) -> ScrapCategory | None:
    """The category with this name (any capitalisation), made if it doesn't exist yet."""
    name = _clip(name, 40)
    if not name:
        return None
    existing = ScrapCategory.objects.filter(name__iexact=name).first()
    if existing:
        if existing.hidden:
            existing.hidden = False
            existing.save(update_fields=["hidden"])
        return existing
    return ScrapCategory.objects.create(name=name, created_by=created_by)


@transaction.atomic
def apply(result: Plan, *, importer: str) -> int:
    categories: dict[str, ScrapCategory | None] = {}
    entries = []
    for row in result.new_rows:
        key = row.category.lower()
        if key not in categories:
            categories[key] = category_for(row.category, importer)
        entries.append(ScrapEntry(
            description=row.description, weight_lbs=row.weight, category=categories[key], note=row.note,
            source=ScrapEntry.Source.IMPORT, weighed_by=row.weighed_by, weighed_at=row.weighed_at, added_by=importer,
            moved_by=row.moved_by, moved_at=row.moved_at, import_key=row.key,
            drop_off=row.drop_off, company=row.company if row.drop_off == ScrapEntry.DropOff.COMPANY else "",
        ))
    ScrapEntry.objects.bulk_create(entries, batch_size=500)
    return len(entries)
