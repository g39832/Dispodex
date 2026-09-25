"""eBay Script Builder: turn an item into a ChatGPT prompt and a final listing script."""
from __future__ import annotations

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
            "Return:",
            "1. A recommended eBay title, 80 characters max",
            "2. A concise description",
            "3. Key item specifics, one per line",
            "4. Any missing facts worth researching",
            "5. A short shipping or packaging note if it is actually helpful",
            "",
            "Inventory record:",
            "\n".join(f"- {line}" for line in lines),
        ]
    )


def build_final_script(chatgpt_text: str) -> str:
    text = (chatgpt_text or "").strip()
    if not text:
        return "Paste the ChatGPT output first."
    return f"{final_boilerplate()}\n\n{text}"


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
    if chatgpt.strip() and not final:
        final = build_final_script(chatgpt)
    return {
        "sku": sku_norm,
        "found": item is not None,
        "facts": [{"label": label, "value": value} for label, value in (source_facts(item) if item else [])],
        "prompt_text": prompt,
        "chatgpt_text": chatgpt,
        "final_text": final,
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
