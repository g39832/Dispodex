"""SKU helpers shared by every app.

A SKU is matched by its *normalized* form everywhere (upper-case, trimmed),
exactly like the PHP app did, so "abc-1 " and "ABC-1" are the same item.
"""
import re

_DIR_UNSAFE = re.compile(r"[^A-Z0-9_-]+")
_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def normalize_sku(value: object) -> str:
    return str(value or "").strip().upper()


def sku_directory(sku: str) -> str:
    """Folder name used for a SKU's photos (same rule as the PHP app)."""
    cleaned = _DIR_UNSAFE.sub("_", normalize_sku(sku)).strip("_")
    return cleaned or "UNASSIGNED"


def sanitize_filename(name: str, fallback: str = "photo") -> str:
    cleaned = _FILENAME_UNSAFE.sub("_", (name or "").strip()).strip("._-")
    return cleaned or fallback


def escape_like(value: str) -> str:
    """Escape % and _ so user text is matched literally in LIKE queries."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
