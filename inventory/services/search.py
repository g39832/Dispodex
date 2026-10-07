"""Search and filtering shared by Lookup, the exports, autocomplete and Ctrl+K."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Count, Exists, OuterRef, Q, QuerySet
from django.utils import timezone

from core.skus import normalize_sku
from inventory.models import Condition, Functional, Item, Photo, Status

MAX_QUERY_LENGTH = 100
MAX_FIELD_LENGTH = 80

# Everything the main search box looks through. Each word typed must appear in at least one of these.
SEARCH_FIELDS = [
    "sku", "what_is_it", "brand_model", "serial_number", "fcc_id", "cpu", "ram", "ssd_gb", "os", "graphics_card",
    "screen_resolution", "battery_health", "notes", "where_it_goes", "source", "ebay_category",
    "ebay_category_path", "what_box", "in_ebay_room",
]

# "More filters": one box per field. URL name -> (model field, label shown to people).
TEXT_FILTERS = {
    "what": ("what_is_it", "What is it"),
    "brand": ("brand_model", "Brand & model"),
    "cpu": ("cpu", "CPU"),
    "ram": ("ram", "RAM"),
    "storage": ("ssd_gb", "Storage"),
    "os": ("os", "OS"),
    "gpu": ("graphics_card", "Graphics card"),
    "serial": ("serial_number", "Serial number"),
    "fcc": ("fcc_id", "FCC ID"),
    "location": ("where_it_goes", "Location"),
    "source": ("source", "Came from"),
    "notes": ("notes", "Notes"),
}
SUGGESTION_LIMIT = 40
PALETTE_LIMIT = 50

SORTS = {
    "updated": ("-updated_at", "-id"),
    "price_asc": ("price", "-id"),
    "price_desc": ("-price", "-id"),
    "sku": ("sku_normalized",),
}


def _date(value) -> date | None:
    try:
        text = str(value or "").strip()
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def _decimal(value) -> Decimal | None:
    try:
        text = str(value or "").strip()
        return Decimal(text) if text else None
    except InvalidOperation:
        return None


@dataclass
class ItemFilters:
    """The Lookup filters, parsed from a query string. Also used by exports."""

    q: str = ""
    status: str = ""
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    sort: str = "updated"
    scope: str = "all"  # "all" or "active" (= not sold)
    stale_days: int = 0
    gap: str = ""  # "no-photos" or "no-price"
    extra: dict = field(default_factory=dict)
    fields: dict = field(default_factory=dict)  # TEXT_FILTERS name -> text
    condition: str = ""
    functional: str = ""
    ready: str = ""  # "yes" / "no"
    received_from: date | None = None
    received_to: date | None = None

    @classmethod
    def from_query(cls, params) -> "ItemFilters":
        status = (params.get("status") or "").strip().lower()
        sort = (params.get("sort") or "updated").strip()
        scope = (params.get("scope") or "all").strip().lower()
        try:
            stale = max(0, int(params.get("stale") or 0))
        except ValueError:
            stale = 0
        gap = (params.get("gap") or "").strip()
        condition = (params.get("condition") or "").strip().capitalize()
        functional = (params.get("functional") or "").strip().capitalize()
        ready = (params.get("ready") or "").strip().lower()
        fields = {}
        for name in TEXT_FILTERS:
            text = " ".join((params.get(name) or "").split())[:MAX_FIELD_LENGTH]
            if text:
                fields[name] = text
        return cls(
            fields=fields,
            condition=condition if condition in Condition.values else "",
            functional=functional if functional in Functional.values else "",
            ready=ready if ready in {"yes", "no"} else "",
            received_from=_date(params.get("received_from")),
            received_to=_date(params.get("received_to")),
            q=(params.get("q") or params.get("sku") or "").strip()[:MAX_QUERY_LENGTH],
            status=status if status in Status.values else "",
            min_price=_decimal(params.get("min_price")),
            max_price=_decimal(params.get("max_price")),
            sort=sort if sort in SORTS else "updated",
            scope=scope if scope in {"all", "active"} else "all",
            stale_days=stale,
            gap=gap if gap in {"no-photos", "no-price", "low-res"} else "",
        )

    def as_query(self) -> dict:
        data = {
            "q": self.q,
            "status": self.status,
            "min_price": "" if self.min_price is None else str(self.min_price),
            "max_price": "" if self.max_price is None else str(self.max_price),
            "sort": "" if self.sort == "updated" else self.sort,
            "scope": "" if self.scope == "all" else self.scope,
            "stale": str(self.stale_days) if self.stale_days else "",
            "gap": self.gap,
            **self.fields,
            "condition": self.condition,
            "functional": self.functional,
            "ready": self.ready,
            "received_from": self.received_from.isoformat() if self.received_from else "",
            "received_to": self.received_to.isoformat() if self.received_to else "",
        }
        return {k: v for k, v in data.items() if v}

    @property
    def advanced_count(self) -> int:
        """How many "More filters" are in use (shown on the button)."""
        return (len(self.fields) + bool(self.condition) + bool(self.functional) + bool(self.ready)
                + bool(self.received_from) + bool(self.received_to) + bool(self.gap) + bool(self.stale_days))

    def active_chips(self) -> list[dict]:
        """The "More filters" in use, as removable chips: label, value and the query without it."""
        chips = []
        base = self.as_query()
        def chip(key, label, value):
            rest = {k: v for k, v in base.items() if k != key}
            chips.append({"key": key, "label": label, "value": value, "query": rest})
        for name, text in self.fields.items():
            chip(name, TEXT_FILTERS[name][1], text)
        if self.condition:
            chip("condition", "Condition", self.condition)
        if self.functional:
            chip("functional", "Functional", self.functional)
        if self.ready:
            chip("ready", "Ready", "Yes" if self.ready == "yes" else "No")
        if self.received_from:
            chip("received_from", "Received from", self.received_from.strftime("%b %d, %Y"))
        if self.received_to:
            chip("received_to", "Received until", self.received_to.strftime("%b %d, %Y"))
        if self.gap:
            chip("gap", "Photos" if self.gap == "low-res" else "Missing",
                 {"no-photos": "photos", "no-price": "price", "low-res": "low-res, to retake"}[self.gap])
        if self.stale_days:
            chip("stale", "Untouched", f"{self.stale_days}+ days")
        return chips

    @property
    def is_filtered(self) -> bool:
        return bool(self.q or self.status or self.min_price is not None or self.max_price is not None
                    or self.scope == "active" or self.stale_days or self.gap or self.advanced_count)


def filter_items(filters: ItemFilters, base: QuerySet | None = None) -> QuerySet:
    qs = base if base is not None else Item.objects.all()
    qs = qs.exclude(sku_normalized="")
    if filters.q:
        # Every word must match somewhere: "dell i5 16gb" finds Dell laptops with an i5 and 16 GB.
        # The whole text is also tried as a SKU prefix, so SKUs with spaces still work.
        whole_sku = Q(sku_normalized__startswith=normalize_sku(filters.q))
        words = Q()
        for word in filters.q.split():
            any_field = Q(sku_normalized__startswith=normalize_sku(word))
            for name in SEARCH_FIELDS:
                any_field |= Q(**{f"{name}__icontains": word})
            words &= any_field
        qs = qs.filter(whole_sku | words)
    for name, text in filters.fields.items():
        qs = qs.filter(**{f"{TEXT_FILTERS[name][0]}__icontains": text})
    if filters.condition:
        qs = qs.filter(condition=filters.condition)
    if filters.functional:
        qs = qs.filter(functional=filters.functional)
    if filters.ready:
        qs = qs.filter(ready=filters.ready == "yes")
    if filters.received_from:
        qs = qs.filter(date_received__gte=filters.received_from)
    if filters.received_to:
        qs = qs.filter(date_received__lte=filters.received_to)
    if filters.status:
        qs = qs.filter(status=filters.status)
    if filters.scope == "active":
        qs = qs.exclude(status=Status.SOLD)
    if filters.min_price is not None:
        qs = qs.filter(price__gte=filters.min_price)
    if filters.max_price is not None:
        qs = qs.filter(price__lte=filters.max_price)
    if filters.stale_days:
        qs = qs.filter(updated_at__lt=timezone.now() - timedelta(days=filters.stale_days))
    if filters.gap == "no-price":
        qs = qs.filter(price__isnull=True)
    elif filters.gap == "low-res":
        qs = qs.filter(Exists(Photo.objects.filter(sku_normalized=OuterRef("sku_normalized"), low_res=True)))
    elif filters.gap == "no-photos":
        qs = qs.annotate(_has_photo=Exists(Photo.objects.filter(sku_normalized=OuterRef("sku_normalized")))).filter(
            _has_photo=False
        )
    return qs.order_by(*SORTS[filters.sort])


def distinct_values(field_name: str, limit: int = 200) -> list[str]:
    """Values already used for a field (for the "More filters" suggestions), most common first."""
    rows = (
        Item.objects.exclude(**{field_name: ""}).values(field_name)
        .annotate(n=Count("id")).order_by("-n", field_name)[:limit]
    )
    seen, values = set(), []
    for row in rows:
        text = " ".join(str(row[field_name]).split())
        if text and text.lower() not in seen:
            seen.add(text.lower())
            values.append(text)
    return values


def photo_counts(skus) -> dict[str, int]:
    rows = Photo.objects.filter(sku_normalized__in=set(skus)).values("sku_normalized").annotate(n=Count("id"))
    return {row["sku_normalized"]: row["n"] for row in rows}


def suggestions(term: str) -> list[dict]:
    """Autocomplete: SKUs that start with the text, or whose description contains it."""
    term = (term or "").strip()[:MAX_QUERY_LENGTH]
    if not term:
        return []
    qs = (
        Item.objects.filter(
            Q(sku_normalized__startswith=normalize_sku(term)) | Q(sku__istartswith=term)
            | Q(what_is_it__icontains=term) | Q(brand_model__icontains=term)
        )
        .order_by("-updated_at", "-id")
        .values("sku", "what_is_it")[:SUGGESTION_LIMIT]
    )
    seen, results = set(), []
    for row in qs:
        sku = row["sku"].strip()
        if not sku or sku in seen:
            continue
        seen.add(sku)
        label = f"{sku} — {row['what_is_it']}" if row["what_is_it"] else sku
        results.append({"value": sku, "label": label})
    return results


PALETTE_FIELDS = [
    "sku", "serial_number", "brand_model", "what_is_it", "notes", "where_it_goes",
    "status", "cpu", "ram", "ssd_gb", "graphics_card", "os", "battery_health", "source",
]


def _palette_row(item: Item) -> dict:
    return {
        "sku": item.sku,
        "sku_normalized": item.sku_normalized,
        "status": item.status,
        "status_label": item.status_label,
        "what_is_it": item.what_is_it,
        "brand_model": item.brand_model,
        "serial_number": item.serial_number,
        "where_it_goes": item.where_it_goes,
        "cpu": item.cpu,
        "ram": item.ram,
        "ssd_gb": item.ssd_gb,
        "price": item.price_display,
        "updated_at": item.updated_at.isoformat(),
    }


def palette_search(term: str) -> list[dict]:
    """Ctrl+K search across SKU, serial, model, specs and notes, best matches first."""
    term = (term or "").strip()[:200]
    if not term:
        return []
    query = Q()
    for name in PALETTE_FIELDS:
        query |= Q(**{f"{name}__icontains": term})
    items = list(Item.objects.filter(query).exclude(sku_normalized="").order_by("-updated_at", "-id")[:PALETTE_LIMIT * 2])
    upper = term.upper()

    def score(item: Item) -> int:
        sku, serial = item.sku_normalized, item.serial_number.upper()
        if sku == upper or serial == upper:
            return 1000
        if sku.startswith(upper) or (serial and serial.startswith(upper)):
            return 700
        model = item.brand_model.lower()
        if model.startswith(term.lower()):
            return 500
        if term.lower() in model:
            return 400
        if term.lower() in item.what_is_it.lower():
            return 300
        return 100

    items.sort(key=lambda it: (-score(it), -it.updated_at.timestamp()))
    return [_palette_row(item) for item in items[:PALETTE_LIMIT]]


def palette_recent() -> list[dict]:
    return [_palette_row(item) for item in Item.objects.exclude(sku_normalized="").order_by("-updated_at", "-id")[:20]]
