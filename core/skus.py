"""SKU helpers shared by every app.

A SKU is matched by its *normalized* form everywhere (upper-case, trimmed),
exactly like the PHP app did, so "abc-1 " and "ABC-1" are the same item.
"""
import re

_DIR_UNSAFE = re.compile(r"[^A-Z0-9_-]+")
_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def normalize_sku(value: object) -> str:
    return str(value or "").strip().upper()


# What a SKU may contain (after normalizing): starts with a letter or digit, then
# letters, digits, spaces, - _ ( ) , only. Covers every SKU already in the data;
# keeps out stray keystrokes like "./'G-1000-19" that break links.
SKU_PATTERN = r"[A-Za-z0-9][A-Za-z0-9 _(),\-]*"
_SKU_OK = re.compile(SKU_PATTERN)
SKU_RULE = "A SKU must start with a letter or number and use only letters, numbers, spaces, - _ ( ) or commas."


def sku_problem(sku: str) -> str | None:
    """Why this (normalized) SKU can't be saved, or None if it's fine."""
    if not sku:
        return "Please fill in the SKU."
    if len(sku) > 64:
        return "A SKU can be at most 64 characters."
    if not _SKU_OK.fullmatch(sku):
        return SKU_RULE
    return None


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
