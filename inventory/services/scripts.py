"""eBay Script Builder: turn an item into a ChatGPT prompt and a final listing script."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from core.skus import normalize_sku
from inventory.models import IntakeDraft, Item, ScriptCache

# The shop's own listing policy text (shipping times, testing, returns...) is business
# material, so it lives in a private file next to the database, not in the code:
# data/ebay_boilerplate.txt (or EBAY_BOILERPLATE_FILE in .env). This is used until one exists.
DEFAULT_BOILERPLATE = "\n".join(
    [
        "Please Read This First",
        "Describe your store's listing policies here: condition grading, testing, included accessories,",
        "data wiping, shipping times and returns.",
        "",
        "To use your own text, save it as data/ebay_boilerplate.txt (plain text, UTF-8).",
        "",
        "—",
    ]
)


def final_boilerplate() -> str:
    """The text placed above every final eBay description."""
    path = Path(settings.PINKSHEET["EBAY_BOILERPLATE_FILE"])
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return DEFAULT_BOILERPLATE
    return text.rstrip("\r\n") or DEFAULT_BOILERPLATE


MAX_BOILERPLATE = 20000


def save_boilerplate(text: str) -> None:
    """Write the shop's listing notes file (written whole, so a reader never sees half a file)."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cleaned = "\n".join(line.rstrip() for line in lines).strip("\n")
    path = Path(settings.PINKSHEET["EBAY_BOILERPLATE_FILE"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(cleaned + "\n", encoding="utf-8", newline="\n")
    temp.replace(path)


def using_example_boilerplate() -> bool:
    """True when the shop's own listing notes file is missing, so the generic example is used."""
    return final_boilerplate() == DEFAULT_BOILERPLATE


TITLE_LIMIT = 80

# Item fields given to ChatGPT as "provided specs" (the old Dispo.list builder's list, with the
# fields Dispodex keeps). Condition and testing are left out: the rules say not to mention them.
SPEC_FIELDS = [
    ("brand_model", "Brand / Model"),
    ("what_is_it", "Type"),
    ("cpu", "Processor"),
    ("ram", "RAM"),
    ("ssd_gb", "Storage"),
    ("graphics_card", "Graphics Card"),
    ("screen_resolution", "Screen Resolution"),
    ("fcc_id", "FCC ID"),
    ("os", "Operating System"),
    ("battery_health", "Battery Health"),
    ("cords_adapters", "Included Cables / Adapters"),
]
_EMPTY_VALUES = {"", "n/a", "na", "none", "-", "—", "null", "unknown"}


def provided_specs(item: Item) -> list[str]:
    lines = []
    for name, label in SPEC_FIELDS:
        value = str(getattr(item, name, "") or "").strip()
        if value.lower() not in _EMPTY_VALUES:
            lines.append(f"{label}: {value}")
    return lines


def build_prompt(sku: str, item: Item) -> str:
    """The old Dispo.list eBay script prompt: ChatGPT writes the whole listing, shop notes included."""
    specs = "\n".join(provided_specs(item))
    return f"""Generate a concise eBay listing for this item. ONLY include information that is explicitly provided below - do not add extra specs or features not listed here.

PROVIDED SPECS:
{specs}
SKU: {sku}

INSTRUCTIONS:
1. Start with a recommended eBay title (max {TITLE_LIMIT} characters) using only the brand, model, and key provided specs.

2. FIRST, include this EXACT boilerplate text (copy it exactly as shown):

{final_boilerplate()}

3. THEN, add a "Product Details" section with:
   - Brand and model
   - Battery health if provided
   - Charger/cables/accessories if specified
   - UPC and MPN only if you can verify them
   - Don't mention storage unless it's explicitly provided
   - If something is not provided, only include stuff that is guaranteed to be true

4. End the Product Details section with: Inventory Number: {sku}

5. After the product details, add a brief section with:
   - Suggested eBay price based on similar sold items
   - Recommended USPS shipping method with estimated cost

RULES:
- Be concise - only include provided information
- No sales language or opinions
- No condition mentions
- No warranty mentions
- No operating system unless specified
- Do not invent specs not provided above"""


_TITLE_HEADING = re.compile(r"^(?:(?:a|the|recommended|suggested|ebay|listing)\s+)*title\b")
_CHATTER = re.compile(r"^(?:sure|here|certainly|absolutely|of course|okay|ok)\b", re.I)


def _plain(line: str) -> str:
    """One line without Markdown: **bold**, `code`, # headings; * bullets become - bullets."""
    line = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), line.rstrip())
    line = line.replace("**", "").replace("`", "")
    line = re.sub(r"^\s*#+\s*", "", line)
    return re.sub(r"^(\s*)[*•]\s+", r"\1- ", line)


def _tidy(lines: list[str]) -> str:
    """Join lines, with at most one blank line in a row and none at either end."""
    text = "\n".join(_plain(line) for line in lines).strip()
    return re.sub(r"\n\s*\n(\s*\n)+", "\n\n", text)


def _words(line: str) -> str:
    """A line's words for matching headings: lowercase, no Markdown, numbering or punctuation."""
    text = re.sub(r"^\d+[.)]\s*", "", _plain(line).strip())
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split())


def _title_heading(line: str) -> tuple[bool, str]:
    """(is a title heading, the title when it is on the same line)."""
    plain = re.sub(r"^\d+[.)]\s*", "", _plain(line).strip())
    head, colon, rest = plain.partition(":")
    words = " ".join(re.sub(r"\(.*?\)", " ", head).lower().split())
    if len(words.split()) <= 6 and _TITLE_HEADING.match(words):
        return True, rest.strip() if colon else ""
    return False, ""


def _unquote(title: str) -> str:
    if len(title) > 1 and (title[0], title[-1]) in {('"', '"'), ("'", "'"), ("“", "”")}:
        return title[1:-1].strip()  # quoted as a whole, but keep an inch mark like 15.6"
    return title


@dataclass
class ParsedAnswer:
    title: str = ""
    description: str = ""
    notes: str = ""


def parse_answer(text: str) -> ParsedAnswer:
    """Split ChatGPT's listing into the title, the description and the notes for staff.

    The answer follows the prompt: a recommended title, the shop notes, Product Details ending
    with "Inventory Number: SKU", then a suggested price and shipping method. The price and
    shipping part is for staff, not buyers. Chatter before the title is dropped.
    """
    lines = (text or "").replace("\r\n", "\n").split("\n")
    first_note = next((line for line in final_boilerplate().split("\n") if line.strip()), "")
    notes_start = _words(first_note)

    # Everything after the "Inventory Number" line: suggested price and shipping.
    inventory = [i for i, line in enumerate(lines) if _words(line).startswith(("inventory number", "inventory no"))]
    staff = lines[inventory[-1] + 1:] if inventory else []
    body = lines[: inventory[-1] + 1] if inventory else lines

    shop_notes = next((i for i, line in enumerate(body) if notes_start and _words(line) == notes_start), None)
    details = next((i for i, line in enumerate(body) if _words(line).startswith("product details")), None)
    listing_start = next((i for i in (shop_notes, details) if i is not None), None)

    # The title: a "Title:" heading near the top, else the last plain line before the listing starts.
    title, after_title = "", 0
    for i, line in enumerate(body[: listing_start if listing_start is not None else 12]):
        is_heading, same_line = _title_heading(line)
        if is_heading:
            following = next((j for j in range(i + 1, len(body)) if body[j].strip()), None)
            if same_line:
                title, after_title = same_line, i + 1
            elif following is not None:
                title, after_title = _plain(body[following]).strip(), following + 1
            break
    if not title and listing_start:
        lead = [_plain(line).strip() for line in body[:listing_start]]
        lead = [line for line in lead if line and not line.endswith(":") and not _CHATTER.match(line)]
        if lead and len(lead[-1]) <= 120:
            title = lead[-1]

    if details is not None:
        # Use the shop notes exactly as written, even if ChatGPT reworded its copy.
        description = f"{final_boilerplate()}\n\n{_tidy(body[details:])}"
    elif shop_notes is not None:
        description = _tidy(body[shop_notes:])
    else:
        # An answer without the shop notes (e.g. to an older prompt): add them on top.
        rest = _tidy(body[after_title:])
        description = f"{final_boilerplate()}\n\n{rest}" if rest else ""
    return ParsedAnswer(title=_unquote(title), description=description, notes=_tidy(staff))


def build_listing(chatgpt_text: str) -> dict:
    """The eBay title, the final description and the notes for staff, from ChatGPT's answer."""
    parsed = parse_answer(chatgpt_text)
    return {
        "title": parsed.title,
        "title_length": len(parsed.title),
        "title_limit": TITLE_LIMIT,
        "final_text": parsed.description,
        "staff_notes": parsed.notes,
    }


def build_final_script(chatgpt_text: str) -> str:
    if not (chatgpt_text or "").strip():
        return "Paste the ChatGPT output first."
    return build_listing(chatgpt_text)["final_text"]


def _old_style_final(chatgpt_text: str, final_text: str) -> bool:
    """True for a final saved before answers were parsed: the shop notes plus the answer pasted as-is."""
    text = (chatgpt_text or "").strip()
    return bool(text) and final_text.strip() == f"{final_boilerplate()}\n\n{text}".strip()


def source_facts(item: Item) -> list[tuple[str, str]]:
    """The short 'Source facts' panel shown next to the prompt."""
    pairs = [
        ("SKU", item.sku_normalized),
        ("Status", item.status_label),
        ("What is it?", item.what_is_it),
        ("Brand / Model", item.brand_model),
        ("RAM", item.ram),
        ("SSD GB", item.ssd_gb),
        ("CPU", item.cpu),
        ("OS", item.os),
        ("Battery Health", item.battery_health),
        ("Price", item.price_display),
        ("Notes", item.notes),
    ]
    return [(label, value) for label, value in pairs if str(value or "").strip()]


def load_script(sku: str, fresh_prompt: bool = False) -> dict:
    """Everything the builder page needs for one SKU. ``fresh_prompt`` rebuilds the prompt from the item."""
    sku_norm = normalize_sku(sku)
    item = Item.objects.filter(sku_normalized=sku_norm).first()
    cache = ScriptCache.objects.filter(sku_normalized=sku_norm).first()
    prompt, chatgpt, final = "", "", ""
    if cache:
        prompt, chatgpt, final = cache.prompt_text, cache.chatgpt_text, cache.final_text
    else:
        # The PHP builder stored its text inside autosave drafts; pick that up if present.
        draft = IntakeDraft.objects.filter(sku_normalized=sku_norm).first()
        if draft and isinstance(draft.payload, dict):
            prompt = str(draft.payload.get("prompt_output") or "")
            chatgpt = str(draft.payload.get("chatgpt_output") or "")
            final = str(draft.payload.get("final_output") or "")
    if item and (fresh_prompt or not prompt):
        prompt = build_prompt(sku_norm, item)
    listing = build_listing(chatgpt)
    if chatgpt.strip() and (not final or _old_style_final(chatgpt, final)):
        final = listing["final_text"]
    return {
        "sku": sku_norm,
        "found": item is not None,
        "facts": [{"label": label, "value": value} for label, value in (source_facts(item) if item else [])],
        "prompt_text": prompt,
        "chatgpt_text": chatgpt,
        "final_text": final,
        "title": listing["title"],
        "title_length": listing["title_length"],
        "title_limit": listing["title_limit"],
        "staff_notes": listing["staff_notes"],
        "saved_at": cache.updated_at.isoformat() if cache else None,
    }


def save_script(sku: str, prompt_text: str, chatgpt_text: str, final_text: str, sku_display: str = "") -> ScriptCache:
    sku_norm = normalize_sku(sku)
    cache, _ = ScriptCache.objects.update_or_create(
        sku_normalized=sku_norm,
        defaults={
            "sku_display": (sku_display or sku_norm).strip()[:64],
            "prompt_text": prompt_text or "",
            "chatgpt_text": chatgpt_text or "",
            "final_text": final_text or "",
            "updated_at": timezone.now(),
        },
    )
    return cache
