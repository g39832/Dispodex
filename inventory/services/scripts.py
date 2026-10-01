"""eBay Script Builder: turn an item into a ChatGPT prompt and a final listing script."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from core.skus import normalize_sku
from inventory.models import IntakeDraft, Item, ScriptCache

FACT_LABELS = [
    ("sku", "SKU"),
    ("status", "Status"),
    ("what_is_it", "What Is It"),
    ("date_received", "Date Received"),
    ("source", "Source"),
    ("functional", "Functional"),
    ("condition", "Condition"),
    ("cords_adapters", "Cords Adapters"),
    ("keep_items_together", "Keep Items Together"),
    ("picture_taken", "Picture Taken"),
    ("power_on", "Power On"),
    ("brand_model", "Brand Model"),
    ("ram", "RAM"),
    ("ssd_gb", "SSD GB"),
    ("cpu", "CPU"),
    ("os", "OS"),
    ("battery_health", "Battery Health"),
    ("graphics_card", "Graphics Card"),
    ("screen_resolution", "Screen Resolution"),
    ("where_it_goes", "Where It Goes"),
    ("ebay_status", "eBay Status"),
    ("price", "Price"),
    ("in_ebay_room", "In eBay Room"),
    ("what_box", "What Box"),
    ("notes", "Notes"),
]

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


def using_example_boilerplate() -> bool:
    """True when the shop's own listing notes file is missing, so the generic example is used."""
    return final_boilerplate() == DEFAULT_BOILERPLATE


def item_facts(item: Item) -> dict[str, str]:
    facts = {}
    for field, _label in FACT_LABELS:
        if field == "price":
            value = "" if item.price is None else str(item.price)
        elif field == "date_received":
            value = item.date_received.isoformat() if item.date_received else ""
        else:
            value = getattr(item, field, "")
        facts[field] = "" if value is None else str(value).strip()
    return facts


def build_prompt(sku: str, item: Item) -> str:
    facts = item_facts(item)
    lines = [f"SKU: {sku}"]
    for field, label in FACT_LABELS:
        if field == "sku":
            continue
        if facts[field]:
            lines.append(f"{label}: {facts[field]}")
    return "\n".join(
        [
            "You are helping me prepare an eBay listing from an internal inventory record.",
            "",
            "Use the facts below as the source of truth. Do not invent details. If a field is missing, omit it.",
            "If the item looks like a computer or electronics device, you may research missing public specs such as model family, UPC, MPN, dimensions, storage type, and ports using reliable sources.",
            "Keep the result factual and neutral. Do not use sales language or unsupported claims.",
            "",
            "Reply in plain text (no Markdown, no ** or #) in exactly this format, with these four headings:",
            "",
            "TITLE:",
            f"(the eBay title, {TITLE_LIMIT} characters max)",
            "",
            "DESCRIPTION:",
            "(a concise description for buyers, in short paragraphs)",
            "",
            "ITEM SPECIFICS:",
            "(one per line, as Name: Value)",
            "",
            "NOTES FOR STAFF:",
            "(missing facts worth researching, or a packaging note; this part is not posted on eBay)",
            "",
            "Inventory record:",
            "\n".join(f"- {line}" for line in lines),
        ]
    )


TITLE_LIMIT = 80

# Section headings in ChatGPT's answer, matched on the heading's words (lowercase, no numbering
# or Markdown). Covers the headings the prompt asks for and the older numbered ones
# ("1. A recommended eBay title, 80 characters max", "4. Any missing facts worth researching").
_SECTIONS = [
    ("title", re.compile(r"^(?:ebay |listing |item )?title\b")),
    ("specifics", re.compile(r"^(?:ebay )?(?:item )?specifics\b|^item specs\b|^specifications\b")),
    ("description", re.compile(r"^(?:concise |short |item |product |listing |ebay |full )*description\b")),
    ("notes", re.compile(
        r"^(?:notes? for (?:staff|us|the team)|staff notes?|internal notes?|missing (?:facts|details|info)"
        r"|facts worth researching|things to research|research notes?|(?:short )?(?:shipping|packaging)\b)"
    )),
]
_FILLER = re.compile(r"^(?:a|an|the|your|my|any|key|suggested|recommended|proposed|final|optional)\s+")


def _heading(line: str) -> tuple[str, str] | None:
    """(section, text after the colon) when ``line`` is a section heading, else None."""
    text = re.sub(r"^[#>\s]+", "", line.strip())
    text = text.replace("**", "").replace("__", "").strip()
    text = re.sub(r"^\d+[.)]\s*", "", text)
    head, colon, rest = text.partition(":")
    words = re.sub(r"\(.*?\)", " ", head).lower()
    words = " ".join(re.sub(r"[^a-z ]+", " ", words).split())
    while _FILLER.match(words):
        words = _FILLER.sub("", words, count=1)
    if not words or len(words.split()) > 9:
        return None
    for name, pattern in _SECTIONS:
        if pattern.match(words):
            return name, rest.strip() if colon else ""
    return None


def _plain(line: str) -> str:
    """One line without Markdown: **bold**, `code`, # headings; * bullets become - bullets."""
    line = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), line.rstrip())
    line = line.replace("`", "")
    line = re.sub(r"^\s*#+\s*", "", line)
    return re.sub(r"^(\s*)[*•]\s+", r"\1- ", line)


def _tidy(lines: list[str]) -> str:
    """Join lines, with at most one blank line in a row and none at either end."""
    text = "\n".join(_plain(line) for line in lines).strip()
    return re.sub(r"\n\s*\n(\s*\n)+", "\n\n", text)


@dataclass
class ParsedAnswer:
    title: str = ""
    description: str = ""
    specifics: list[str] = field(default_factory=list)
    notes: str = ""


def parse_answer(text: str) -> ParsedAnswer:
    """Split ChatGPT's answer into title, description, item specifics and notes for staff.

    Anything before the first heading ("Sure! Here's your listing:") is dropped. An answer with
    no recognisable headings is used as the description as-is.
    """
    sections: dict[str, list[str]] = {"title": [], "description": [], "specifics": [], "notes": []}
    preamble: list[str] = []
    current = None
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        heading = _heading(line)
        if heading:
            current, rest = heading
            if rest:
                sections[current].append(rest)
        elif current:
            sections[current].append(line)
        else:
            preamble.append(line)
    if current is None:
        sections["description"] = preamble
    title_lines = [_plain(line).strip() for line in sections["title"] if line.strip()]
    specifics = []
    for line in sections["specifics"]:
        line = re.sub(r"^(?:[-*•]|\d+[.)])\s*", "", _plain(line).strip())
        if line:
            specifics.append(line)
    title = title_lines[0] if title_lines else ""
    if len(title) > 1 and (title[0], title[-1]) in {('"', '"'), ("'", "'"), ("“", "”")}:
        title = title[1:-1].strip()  # quoted as a whole, but keep an inch mark like 15.6"
    return ParsedAnswer(
        title=title,
        description=_tidy(sections["description"]),
        specifics=specifics,
        notes=_tidy(sections["notes"]),
    )


def build_listing(chatgpt_text: str) -> dict:
    """The eBay title, the final description (shop notes, description, item specifics) and staff notes."""
    parsed = parse_answer(chatgpt_text)
    body = [parsed.description] if parsed.description else []
    if parsed.specifics:
        body.append("Item Specifics\n" + "\n".join(f"- {line}" for line in parsed.specifics))
    final = f"{final_boilerplate()}\n\n" + "\n\n".join(body) if body else ""
    return {
        "title": parsed.title,
        "title_length": len(parsed.title),
        "title_limit": TITLE_LIMIT,
        "final_text": final,
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
