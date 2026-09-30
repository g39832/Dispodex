"""Turn the imaging app's JSON into a DeviceReport, and its specs into item fields.

The imaging app sends one JSON object per update while it images a computer:

    {"serial": "TEST5500A", "manufacturer": "Dell Inc.", "model": "Latitude 5500",
     "cpu": "Intel Core i5-8365U", "ram_gb": 8, "disk_gb": 256,
     "windows_edition": "Windows 11 Pro", "battery_health_pct": 91,
     "status": "Imaging", "stage": "Hardware detected", "progress": 5, "message": "..."}

Reports are matched by serial number. Once a report is linked to an item (or an
item has the same serial), its specs fill in that item's blank fields. What
staff typed on the intake sheet is never overwritten.
"""
from __future__ import annotations

import re

from django.db import transaction
from django.utils import timezone

from core import actor as actor_context
from imaging.models import DeviceReport
from inventory.models import Item, ItemEvent
from inventory.services import history
from squaresync import queue as square_queue

IMAGING_ACTOR = "Imaging"
MAX_LOG = 50
MAX_PAYLOAD_KEYS = 200

_BRANDS = {
    "dell inc.": "Dell", "dell": "Dell", "hewlett-packard": "HP", "hp": "HP", "hp inc.": "HP",
    "lenovo": "Lenovo", "microsoft corporation": "Microsoft", "apple inc.": "Apple",
    "asustek computer inc.": "ASUS", "acer": "Acer", "samsung electronics co., ltd.": "Samsung",
}
_COMPANY_SUFFIX = re.compile(r",?\s+(inc\.?|corporation|corp\.?|co\.,?\s*ltd\.?|ltd\.?|llc)$", re.IGNORECASE)


class ReportError(Exception):
    """The JSON was rejected; the message is safe to send back."""


def _text(value, limit: int = 255) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    return " ".join(str(value).split())[:limit]


def _number(value) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return float(str(value).strip().rstrip("%"))
    except ValueError:
        return None


def brand(manufacturer: str) -> str:
    name = _text(manufacturer)
    if not name:
        return ""
    known = _BRANDS.get(name.lower())
    if known:
        return known
    name = _COMPANY_SUFFIX.sub("", name)
    return name.title() if name.isupper() and len(name) > 3 else name


def brand_model(manufacturer: str, model: str) -> str:
    make, model = brand(manufacturer), _text(model)
    if make and model.lower().startswith(make.lower()):
        return model
    return " ".join(part for part in (make, model) if part)


def short_cpu(value) -> str:
    """"Intel(R) Core(TM) i5-8365U CPU @ 1.60GHz" -> "i5-8365U", the way items are written."""
    cpu = _text(value)
    cpu = re.sub(r"\((R|TM|C)\)", "", cpu, flags=re.IGNORECASE)
    cpu = re.sub(r"\s*@.*$", "", cpu)
    cpu = re.sub(r"\s+(CPU|Processor)\b", "", cpu, flags=re.IGNORECASE)
    cpu = re.sub(r"\s+with Radeon.*$", "", cpu, flags=re.IGNORECASE)
    cpu = " ".join(cpu.split())
    match = re.match(r"^Intel\s+Core\s+(i[3579]-\w+)$", cpu, re.IGNORECASE)
    return match.group(1) if match else cpu


def _gb(value) -> str:
    number = _number(value)
    if number is None:
        return _text(value)
    return f"{round(number)}GB" if number > 0 else ""


def item_specs(data: dict) -> dict:
    """The item fields this JSON can fill, leaving out anything it didn't send."""
    battery = _number(data.get("battery_health_pct"))
    specs = {
        "serial_number": _text(data.get("serial") or data.get("serial_number"), 128),
        "brand_model": brand_model(data.get("manufacturer"), data.get("model")),
        "cpu": short_cpu(data.get("cpu")),
        "ram": _gb(data.get("ram_gb")),
        "ssd_gb": _gb(data.get("disk_gb")),
        "os": _text(data.get("windows_edition") or data.get("os")),
        "battery_health": f"{round(battery)}%" if battery is not None and data.get("battery_present") is not False else "",
    }
    return {field: value for field, value in specs.items() if value}


def fill_item(item: Item, specs: dict, actor: str) -> list[str]:
    """Copy specs into the item's blank fields. Returns the fields that changed."""
    before = history.snapshot(item)
    filled = []
    for field, value in specs.items():
        if not str(getattr(item, field) or "").strip():
            setattr(item, field, value)
            filled.append(field)
    if filled:
        item.updated_at = timezone.now()
        item.save()
        history.record(item, ItemEvent.Action.EDITED, before=before, actor=actor)
        sku = item.sku_normalized
        transaction.on_commit(lambda: square_queue.enqueue(sku))
    return filled


def matching_item(serial: str) -> Item | None:
    return Item.objects.filter(serial_number__iexact=serial).order_by("-updated_at").first() if serial else None


@transaction.atomic
def receive(data, *, reported_by: str = "") -> DeviceReport:
    """Save one JSON update from the imaging app."""
    if not isinstance(data, dict):
        raise ReportError("Send one JSON object per computer.")
    if len(data) > MAX_PAYLOAD_KEYS:
        raise ReportError("That JSON has too many fields.")
    serial = _text(data.get("serial") or data.get("serial_number"), 128).upper()
    if not serial:
        raise ReportError('The JSON needs a "serial" so Dispodex can tell computers apart.')

    report = DeviceReport.objects.select_for_update().filter(serial=serial).first()
    now = timezone.now()
    if report is None:
        report = DeviceReport(serial=serial, first_seen=now)
    progress = _number(data.get("progress"))
    update = {
        "status": _text(data.get("status"), 64),
        "stage": _text(data.get("stage"), 128),
        "progress": None if progress is None else max(0, min(100, round(progress))),
        "message": _text(data.get("message"), 500),
    }
    changed = any(getattr(report, key) != value for key, value in update.items())
    for key, value in update.items():
        setattr(report, key, value)
    report.manufacturer = _text(data.get("manufacturer"), 128) or report.manufacturer
    report.model = _text(data.get("model"), 128) or report.model
    # Later updates may only carry progress: keep hardware details from earlier ones.
    report.payload = {**(report.payload or {}), **data}
    if changed and any(update.values()):
        report.log = ([{"at": now.isoformat(), **update}] + list(report.log or []))[:MAX_LOG]
    report.last_seen = now
    report.reported_by = reported_by[:80]
    if report.item_id is None or not Item.objects.filter(pk=report.item_id).exists():
        report.item = matching_item(serial)
    report.save()
    if report.item is not None:
        fill_item(report.item, item_specs(report.payload), IMAGING_ACTOR)
    return report


@transaction.atomic
def link(report: DeviceReport, item: Item) -> list[str]:
    """Tie a report to an item (a person did this), filling the item's blank specs."""
    serial = item.serial_number.strip()
    if serial and serial.upper() != report.serial:
        raise ReportError(f"{item.sku} already has a different serial number ({serial}).")
    report.item = item
    report.save(update_fields=["item"])
    return fill_item(item, item_specs(report.payload), actor_context.current())


def link_matches(reports) -> None:
    """Link unlinked reports whose serial was since typed on an item."""
    for report in reports:
        if report.item_id is None:
            item = matching_item(report.serial)
            if item is not None:
                report.item = item
                report.save(update_fields=["item"])
