"""The intake sheet: create and edit items."""
from __future__ import annotations

import csv
import io
import logging

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.skus import normalize_sku
from inventory.forms import IntakeForm
from inventory.models import (
    CompatibleOS,
    Condition,
    Functional,
    Item,
    Photo,
    Status,
    YesNo,
)
from inventory.services import history
from inventory.services.items import ItemError, save_intake
from squaresync.config import get_config

lookup_log = logging.getLogger("pinksheet.lookup")

BASE_WHAT_IS_IT = ["Laptop", "Desktop", "Mini PC"]
FORM_FIELDS = IntakeForm.Meta.fields + ["price"]
BOOL_FIELDS = {"diagnostics_test_ran", "wifi_card_installed"}


def _log_lookup(request, sku: str) -> None:
    buffer = io.StringIO()
    csv.writer(buffer).writerow([timezone.now().isoformat(), sku, request.META.get("REMOTE_ADDR", "")])
    lookup_log.info(buffer.getvalue().strip())


def item_values(item: Item | None) -> dict:
    values = {name: "" for name in FORM_FIELDS}
    values.update({name: False for name in BOOL_FIELDS})
    values["status"] = Status.INTAKE
    values["quantity"] = 1
    if item is None:
        return values
    for name in FORM_FIELDS:
        value = getattr(item, name)
        if name == "date_received":
            value = value.isoformat() if value else ""
        elif name == "price":
            value = "" if value is None else f"{value:.2f}"
        values[name] = value
    return values


def posted_values(post) -> dict:
    values = {name: post.get(name, "") for name in FORM_FIELDS}
    for name in BOOL_FIELDS:
        values[name] = name in post
    return values


def what_is_it_options(current: str) -> list[str]:
    options = list(BASE_WHAT_IS_IT)
    existing = (
        Item.objects.exclude(what_is_it="").values_list("what_is_it", flat=True).distinct().order_by("what_is_it")[:120]
    )
    for label in existing:
        label = label.strip()
        if label and label not in options:
            options.append(label)
    if current and current not in options:
        options.append(current)
    return options


@require_http_methods(["GET", "POST"])
def intake(request):
    lookup_sku = normalize_sku(request.GET.get("sku"))
    item = Item.objects.filter(sku_normalized=lookup_sku).first() if lookup_sku else None
    errors: list[str] = []

    if request.method == "POST":
        form = IntakeForm(request.POST)
        values = posted_values(request.POST)
        if form.is_valid():
            item_id = request.POST.get("id") or None
            try:
                result = save_intake(form.item_values(), item_id=int(item_id) if item_id else None)
            except (ItemError, ValueError) as exc:
                errors.append(str(exc))
            else:
                square = get_config()
                note = " Queued for Square." if square.enabled else ""
                if result.created:
                    messages.success(request, f"Saved {result.item.sku} as a new item.{note}")
                else:
                    messages.success(request, f"Saved changes to {result.item.sku}.{note}")
                if request.POST.get("save_and_new"):
                    return redirect(f"{reverse('intake')}?new=1&saved=1")
                return redirect(f"{reverse('intake')}?sku={result.item.sku_normalized}&saved=1")
        else:
            for field_errors in form.errors.values():
                errors.extend(field_errors)
        active_sku = normalize_sku(values.get("sku"))
        item_id_value = request.POST.get("id", "")
    else:
        if lookup_sku:
            _log_lookup(request, lookup_sku)
        values = item_values(item)
        if lookup_sku and item is None:
            values["sku"] = lookup_sku
        active_sku = normalize_sku(values.get("sku"))
        item_id_value = str(item.pk) if item else ""

    photos = list(Photo.objects.filter(sku_normalized=active_sku)) if active_sku else []
    thumb = next((p for p in photos if p.is_thumb), photos[-1] if photos else None)
    context = {
        "page": "intake" if item is None else "item",
        "item": item,
        "item_id": item_id_value,
        "values": values,
        "errors": errors,
        "active_sku": active_sku,
        "photos": photos,
        "history": history.recent_for(item, 30) if item else [],
        "print_thumb": thumb,
        "statuses": Status.choices,
        "legacy_status": values["status"] if values["status"] not in Status.values else "",
        "what_options": what_is_it_options(values.get("what_is_it", "")),
        "functional_choices": Functional.values,
        "yes_no": YesNo.values,
        # (value, colour): worst grades red, middle amber, good ones green.
        "condition_choices": [
            (value, "red" if value in (Condition.SCRAP, Condition.POOR) else "amber" if value == Condition.FAIR else "green")
            for value in Condition.values
        ],
        "compatible_os_choices": CompatibleOS.values,
        "clear_draft": "new" in request.GET,
        "just_saved": "saved" in request.GET,
        "photo_limit_mb": settings.PINKSHEET["PHOTO_MAX_BYTES"] // (1024 * 1024),
        "intake_config": {
            "sku": active_sku,
            "itemId": item_id_value,
            "hasRecord": item is not None,
            "clearDraft": "new" in request.GET,
            "justSaved": "saved" in request.GET,
            "hadErrors": bool(errors),
            "photoLimitBytes": settings.PINKSHEET["PHOTO_MAX_BYTES"],
            "cardUrlBase": request.build_absolute_uri("/card/"),
        },
    }
    return render(request, "inventory/intake.html", context, status=400 if errors else 200)
