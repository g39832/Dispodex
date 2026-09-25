"""ZPL label generation for Zebra printers (printed through QZ Tray).

This is a line-for-line port of the PHP ``assets/zpl.php`` — the test suite
compares its output byte-for-byte against labels produced by the old app, so
printed stickers look exactly the same.

Presets:
  * compact — 1.5" x 0.5" SKU sticker
  * detail  — 2.5" x 1.5" intake label
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

PRESET_OPTIONS = ("compact", "detail")
DPI_OPTIONS = (203, 300)
CODE_OPTIONS = ("qr", "code128", "none")
FONT_MIN, FONT_MAX = 24, 64


@dataclass(frozen=True)
class Preset:
    name: str
    width_in: float
    height_in: float
    base_width: int
    base_height: int
    default_font: int
    default_code: str
    default_details: bool


COMPACT = Preset("SKU sticker", 1.5, 0.5, 304, 102, 42, "qr", False)
DETAIL = Preset("Intake label", 2.5, 1.5, 508, 305, 52, "qr", True)


def get_preset(name: str) -> Preset:
    return DETAIL if name == "detail" else COMPACT


def _php_round(value: float) -> int:
    """PHP's round(): halves go away from zero (Python's round() goes to even)."""
    return int(math.floor(abs(value) + 0.5)) * (1 if value >= 0 else -1)


@dataclass(frozen=True)
class _Config:
    width: int
    height: int
    scale: float


def _config(preset: Preset, dpi: int) -> _Config:
    scale = 300.0 / 203.0 if dpi == 300 else 1.0
    width = _php_round(preset.width_in * 300) if dpi == 300 else preset.base_width
    height = _php_round(preset.height_in * 300) if dpi == 300 else preset.base_height
    return _Config(width, height, scale)


def _x(value: float, config: _Config) -> int:
    return _php_round(value * config.scale)


def sanitize(value) -> str:
    """Strip ZPL control characters and anything that isn't printable ASCII."""
    text = re.sub(r"[\^~\\]", "", str(value))
    raw = text.encode("utf-8")
    cleaned = bytes(b if 0x20 <= b <= 0x7E else (0x20 if b < 0x20 or b == 0x7F else 0x3F) for b in raw)
    return cleaned.decode("ascii").strip(" \t\n\r\0\x0b")


def sanitize_barcode(value) -> str:
    clean = re.sub(r"[^A-Za-z0-9./_-]", "", sanitize(value))[:20]
    return clean or "NO-SKU"


def truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max(0, max_length - 3)] + "..."


def format_date(value) -> str:
    if value is None or value == "":
        return ""
    value = str(value)
    parts = value.split("-")
    if len(parts) == 3 and all(parts):
        return f"{parts[1]}/{parts[2]}/{parts[0][-2:]}"
    return value


def generate_zpl(item: dict) -> str:
    """Build the raw ZPL (``^XA … ^XZ``) for one label.

    ``item`` keys: sku, itemName, description, date, labelPreset, dpi,
    skuFontSize, codeType, showDetails.
    """
    preset = get_preset(item.get("labelPreset") or "compact")
    dpi = int(item.get("dpi") or 203)
    config = _config(preset, dpi)
    font_size = max(FONT_MIN, min(FONT_MAX, int(item.get("skuFontSize") or preset.default_font)))
    code_type = item.get("codeType") or preset.default_code
    show_detail = bool(item.get("showDetails"))

    sku_value = item.get("sku")
    name_value = item.get("itemName")
    safe_sku = truncate(sanitize("NO-SKU" if sku_value is None else sku_value), 24)
    safe_name = truncate(sanitize("Unnamed item" if name_value is None else name_value), 44)
    meta_parts = [p for p in (item.get("description"), format_date(item.get("date"))) if p is not None and p != ""]
    safe_meta = truncate(sanitize(" | ".join(str(p) for p in meta_parts)), 58)
    encoded_sku = sanitize_barcode("NO-SKU" if sku_value is None else sku_value)

    lines = ["^XA", f"^PW{config.width}", f"^LL{config.height}", "^LH0,0"]
    is_sticker = preset.name == "SKU sticker"
    if is_sticker:
        text_width = _x(202, config) if code_type == "qr" else config.width - _x(18, config)
        text_area_height = 66 if code_type == "code128" else preset.base_height
        sku_y = (
            _x(8, config)
            if show_detail
            else max(_x(4, config), _php_round((text_area_height - font_size) / 2) + _x(8, config))
        )
        lines.append(f"^CF0,{_x(font_size, config)}")
        lines.append(f"^FO{_x(9, config)},{sku_y}^FB{text_width},2,{_x(1, config)},L,0^FD{safe_sku}^FS")
        if show_detail:
            detail_y = _x(54, config) if code_type == "code128" else _x(72, config)
            lines.append(f"^CF0,{_x(12, config)}")
            lines.append(f"^FO{_x(10, config)},{detail_y}^FB{text_width},1,0,L,0^FD{truncate(safe_name, 28)}^FS")
    else:
        text_width = _x(340, config) if code_type == "qr" else config.width - _x(32, config)
        text_area_height = 220 if code_type == "code128" else preset.base_height
        sku_y = (
            _x(28, config)
            if show_detail
            else max(_x(12, config), _php_round((text_area_height - font_size) / 2) + _x(10, config))
        )
        lines.append(f"^CF0,{_x(font_size, config)}")
        lines.append(f"^FO{_x(18, config)},{sku_y}^FB{text_width},1,0,L,0^FD{safe_sku}^FS")
        if show_detail:
            lines.append(f"^CF0,{_x(29, config)}")
            lines.append(f"^FO{_x(18, config)},{_x(105, config)}^FB{text_width},2,{_x(4, config)},L,0^FD{safe_name}^FS")
            if safe_meta != "":
                meta_y = _x(180, config) if code_type == "code128" else _x(205, config)
                meta_lines = 2 if code_type == "code128" else 3
                lines.append(f"^CF0,{_x(22, config)}")
                lines.append(
                    f"^FO{_x(18, config)},{meta_y}^FB{text_width},{meta_lines},{_x(3, config)},L,0^FD{safe_meta}^FS"
                )

    if code_type == "qr":
        qr_x = _x(218, config) if is_sticker else _x(365, config)
        qr_y = _x(7, config) if is_sticker else _x(55, config)
        magnification = (4 if is_sticker else 8) if dpi == 300 else (3 if is_sticker else 5)
        lines.append(f"^FO{qr_x},{qr_y}^BQN,2,{magnification}^FDLA,{encoded_sku}^FS")
    elif code_type == "code128":
        barcode_y = _x(70, config) if is_sticker else _x(242, config)
        barcode_height = _x(18, config) if is_sticker else _x(45, config)
        lines.append(f"^FO{_x(12, config)},{barcode_y}^BY{max(1, _x(1, config))},2,{barcode_height}")
        lines.append(f"^BCN,{barcode_height},Y,N,N^FD{encoded_sku}^FS")

    lines.append("^PQ1,0,1,Y")
    lines.append("^XZ")
    return "\r\n".join(lines)


def label_for_item(sku: str, item, preset: str = "compact", dpi: int = 203, code_type: str = "",
                   font_size: int = 0, show_details: bool | None = None) -> dict:
    """Build the label payload for an Item (or just a SKU when the item isn't saved yet)."""
    definition = get_preset(preset)
    data = {
        "sku": sku,
        "itemName": item.what_is_it if item else "",
        "description": item.notes if item else "",
        "date": item.date_received.isoformat() if item and item.date_received else "",
        "labelPreset": preset if preset in PRESET_OPTIONS else "compact",
        "dpi": dpi if dpi in DPI_OPTIONS else 203,
        "skuFontSize": max(FONT_MIN, min(FONT_MAX, font_size)) if font_size else definition.default_font,
        "codeType": code_type if code_type in CODE_OPTIONS else definition.default_code,
        "showDetails": definition.default_details if show_details is None else show_details,
    }
    return {
        "zpl": generate_zpl(data),
        "sku": sku,
        "itemName": data["itemName"],
        "labelPreset": definition.name,
        "widthIn": definition.width_in,
        "heightIn": definition.height_in,
        "dpi": data["dpi"],
    }
