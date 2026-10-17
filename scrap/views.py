"""The Scrap page: weigh things, mark them moved to the scrap side, and see how many pounds went over."""
from __future__ import annotations

import csv
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from core.actor import clean_name
from core.http import json_ok
from core.templatetags.ui import lbs
from operations.sheet_import import SheetError
from scrap import importer, scale
from scrap.models import ScrapCategory, ScrapEntry

ZERO = Decimal("0")
MAX_WAITING_SHOWN = 300
IMPORT_MAX_BYTES = 10 * 1024 * 1024
IMPORT_SESSION_KEY = "scrap_import"
RANGES = [("today", "Today"), ("week", "This week"), ("month", "This month"), ("year", "This year"), ("all", "All time")]


def _demo() -> bool:
    return settings.PINKSHEET["DEMO_MODE"]


def _asks_for_name(request) -> bool:
    return not (request.user.is_authenticated or request.actor_named)


def _who(request) -> str:
    """The signed-in account or saved device name, else the name typed on the form."""
    if _asks_for_name(request):
        return clean_name(request.POST.get("name", ""))
    return request.actor[:80]


def _parse_lbs(text: str) -> Decimal | None:
    try:
        value = Decimal(str(text or "").strip().replace(",", ""))
    except InvalidOperation:
        return None
    if not value.is_finite() or value <= 0 or value >= importer.MAX_WEIGHT:
        return None
    return value.quantize(Decimal("0.01"))


def _period(request) -> tuple[str, datetime | None, datetime | None, str]:
    """(range key, start, end, label) from ?range=… or ?from=…&to=… (local dates, end inclusive)."""
    now = timezone.localtime()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_text, end_text = request.GET.get("from", "").strip(), request.GET.get("to", "").strip()
    if start_text or end_text:
        start_day = importer._parse_date(start_text) if start_text else None
        end_day = importer._parse_date(end_text) if end_text else None
        if start_day or end_day:
            if start_day and end_day and end_day < start_day:
                start_day, end_day = end_day, start_day
            start = timezone.make_aware(datetime.combine(start_day, datetime.min.time())) if start_day else None
            end = timezone.make_aware(datetime.combine(end_day, datetime.min.time())) + timedelta(days=1) if end_day else None
            label = " – ".join(_day_label(day) for day in (start_day, end_day) if day)
            if not end_day:
                label = f"Since {label}"
            elif not start_day:
                label = f"Up to {label}"
            return "custom", start, end, label
    key = request.GET.get("range", "month")
    if key == "today":
        return key, midnight, None, "Today"
    if key == "week":
        return key, midnight - timedelta(days=midnight.weekday()), None, "This week"
    if key == "year":
        return key, midnight.replace(month=1, day=1), None, "This year"
    if key == "all":
        return key, None, None, "All time"
    return "month", midnight.replace(day=1), None, "This month"


def _day_label(day) -> str:
    return f"{day.strftime('%b')} {day.day}, {day.year}"


def _taken_in(text: str):
    """When it was taken in, from the form's date box: now for today (or blank), noon for an
    earlier day. None for a date that can't be read or is in the future."""
    text = str(text or "").strip()
    now = timezone.localtime()
    if not text:
        return now
    day = importer._parse_date(text)
    if day is None or day > now.date():
        return None
    if day == now.date():
        return now
    return timezone.make_aware(datetime.combine(day, datetime.min.time().replace(hour=12)))


def _within(field: str, start, end) -> Q:
    q = Q(**{f"{field}__isnull": False})
    if start:
        q &= Q(**{f"{field}__gte": start})
    if end:
        q &= Q(**{f"{field}__lt": end})
    return q


def _totals(queryset) -> dict:
    data = queryset.aggregate(lbs=Sum("weight_lbs"), n=Count("id"))
    return {"lbs": data["lbs"] or ZERO, "count": data["n"]}


def _filtered_history(request, start, end):
    """Entries weighed or moved in the period, narrowed by the search, category and person filters."""
    filters = {key: request.GET.get(key, "").strip() for key in ("q", "category", "person", "state")}
    entries = ScrapEntry.objects.select_related("category").filter(
        _within("weighed_at", start, end) | _within("moved_at", start, end)
    )
    if filters["q"]:
        q = filters["q"]
        entries = entries.filter(Q(description__icontains=q) | Q(note__icontains=q) | Q(category__name__icontains=q)
                                 | Q(weighed_by__icontains=q) | Q(moved_by__icontains=q))
    if filters["category"] == "none":
        entries = entries.filter(category__isnull=True)
    elif filters["category"].isdigit():
        entries = entries.filter(category_id=int(filters["category"]))
    if filters["person"]:
        entries = entries.filter(Q(weighed_by=filters["person"]) | Q(moved_by=filters["person"]))
    if filters["state"] == "moved":
        entries = entries.filter(moved_at__isnull=False)
    elif filters["state"] == "waiting":
        entries = entries.filter(moved_at__isnull=True)
    return entries, filters


def _people_breakdown(start, end) -> list[dict]:
    """Per person: pounds they weighed and pounds they moved in the period, most first."""
    people: dict[str, dict] = {}
    weighed = ScrapEntry.objects.filter(_within("weighed_at", start, end)).values("weighed_by").annotate(lbs=Sum("weight_lbs"), n=Count("id"))
    for row in weighed:
        people.setdefault(row["weighed_by"], {"name": row["weighed_by"], "weighed": ZERO, "weighed_n": 0, "moved": ZERO, "moved_n": 0})
        people[row["weighed_by"]].update(weighed=row["lbs"] or ZERO, weighed_n=row["n"])
    moved = ScrapEntry.objects.filter(_within("moved_at", start, end)).values("moved_by").annotate(lbs=Sum("weight_lbs"), n=Count("id"))
    for row in moved:
        people.setdefault(row["moved_by"], {"name": row["moved_by"], "weighed": ZERO, "weighed_n": 0, "moved": ZERO, "moved_n": 0})
        people[row["moved_by"]].update(moved=row["lbs"] or ZERO, moved_n=row["n"])
    return sorted(people.values(), key=lambda p: (-(p["weighed"] + p["moved"]), p["name"].lower()))


def _category_breakdown(start, end) -> list[dict]:
    rows = (ScrapEntry.objects.filter(_within("moved_at", start, end)).values("category__name")
            .annotate(lbs=Sum("weight_lbs"), n=Count("id")).order_by("-lbs"))
    total = sum((row["lbs"] or ZERO for row in rows), ZERO)
    return [{"name": row["category__name"] or "No category", "lbs": row["lbs"] or ZERO, "count": row["n"],
             "share": round(float((row["lbs"] or ZERO) / total * 100), 1) if total else 0} for row in rows]


def _visible_categories():
    return ScrapCategory.objects.filter(hidden=False).order_by("name")


def scrap(request):
    range_key, start, end, period_label = _period(request)
    history, filters = _filtered_history(request, start, end)
    page_obj = Paginator(history, 50).get_page(request.GET.get("page"))
    waiting = ScrapEntry.objects.select_related("category").filter(moved_at__isnull=True).order_by("weighed_at", "id")
    keep_period = {key: request.GET.get(key, "") for key in ("range", "from", "to") if request.GET.get(key, "").strip()}
    keep_all = {**keep_period, **{key: value for key, value in filters.items() if value}}
    people = ScrapEntry.objects.values_list("weighed_by", flat=True).distinct()
    movers = ScrapEntry.objects.exclude(moved_by="").values_list("moved_by", flat=True).distinct()
    return render(request, "scrap/scrap.html", {
        "page": "scrap", "demo": _demo(), "ask_name": _asks_for_name(request),
        "range_key": range_key, "ranges": RANGES, "period_label": period_label,
        "from": request.GET.get("from", ""), "to": request.GET.get("to", ""),
        "waiting_totals": _totals(waiting), "waiting": list(waiting[:MAX_WAITING_SHOWN]),
        "waiting_more": max(0, waiting.count() - MAX_WAITING_SHOWN),
        "moved_totals": _totals(ScrapEntry.objects.filter(_within("moved_at", start, end))),
        "weighed_totals": _totals(ScrapEntry.objects.filter(_within("weighed_at", start, end))),
        "all_moved_totals": _totals(ScrapEntry.objects.filter(moved_at__isnull=False)),
        "categories": _visible_categories(), "all_categories": ScrapCategory.objects.order_by("name"),
        "by_category": _category_breakdown(start, end), "by_person": _people_breakdown(start, end),
        "page_obj": page_obj, "filters": filters, "filtered": any(filters.values()),
        "people": sorted(set(people) | set(movers), key=str.lower),
        "period_query": urlencode(keep_period), "page_query": urlencode(keep_all),
        "export_query": urlencode(keep_all),
        "next": request.get_full_path(),
        "total_entries": ScrapEntry.objects.exists(),
        "scale_ready": bool(scale.url()),
        "today": timezone.localdate().isoformat(),
    })


def _back(request, default="scrap"):
    target = request.POST.get("next", "")
    if target.startswith("/scrap/") and "//" not in target:
        return redirect(target)
    return redirect(default)


@require_POST
def weigh(request):
    """Add one weighed thing to the scrap list (waiting to move, or moved straight away)."""
    if _demo():
        messages.error(request, "Adding scrap is turned off in the demo.")
        return _back(request)
    # Without a saved name, the "Taken in by" box also says who is entering it.
    who = _who(request) or clean_name(request.POST.get("weighed_by", ""))
    taken_by = clean_name(request.POST.get("weighed_by", "")) or who
    taken_at = _taken_in(request.POST.get("taken_in", ""))
    weight = _parse_lbs(request.POST.get("weight", ""))
    if not who:
        messages.error(request, "Add your name so everyone knows who entered it.")
    elif weight is None:
        messages.error(request, "Enter the weight in pounds, like 12.5.")
    elif taken_at is None:
        messages.error(request, "Pick the date it was taken in (today or earlier).")
    else:
        now = timezone.now()
        moved_now = request.POST.get("moved_now") == "1"
        entry = ScrapEntry.objects.create(
            description=" ".join(request.POST.get("description", "").split())[:200],
            weight_lbs=weight,
            category=importer.category_for(request.POST.get("category", ""), who),
            note=" ".join(request.POST.get("note", "").split())[:255],
            source=ScrapEntry.Source.SCALE if request.POST.get("source") == "scale" else ScrapEntry.Source.TYPED,
            weighed_by=taken_by, weighed_at=taken_at, added_by=who,
            moved_by=who if moved_now else "", moved_at=max(now, taken_at) if moved_now else None,
        )
        what = f"{lbs(entry.weight_lbs)} lbs" + (f" of {entry.description}" if entry.description else "")
        what += f", taken in by {entry.weighed_by}"
        if moved_now:
            messages.success(request, f"Added {what}, moved to scrap.")
        else:
            messages.success(request, f"Added {what}. It's waiting to be moved to scrap.")
    return _back(request)


@require_POST
def move(request):
    """Mark the ticked entries (or the one button pressed) as moved to the scrap side."""
    if _demo():
        messages.error(request, "Moving scrap is turned off in the demo.")
        return _back(request)
    who = _who(request)
    # A row's own "Moved" button sends only that row; the bottom button sends every ticked row.
    only = request.POST.get("only", "")
    ids = [int(only)] if only.isdigit() else [int(v) for v in request.POST.getlist("entry") if v.isdigit()]
    if not who:
        messages.error(request, "Add your name so everyone knows who moved it.")
    elif not ids:
        messages.error(request, "Tick the items that were moved to scrap first.")
    else:
        entries = ScrapEntry.objects.filter(pk__in=ids, moved_at__isnull=True)
        totals = _totals(entries)
        moved = entries.update(moved_at=timezone.now(), moved_by=who)
        if moved:
            messages.success(request, f"Moved {moved} item{'s' if moved != 1 else ''} to scrap ({lbs(totals['lbs'])} lbs).")
        else:
            messages.info(request, "Those were already marked as moved.")
    return _back(request)


def edit(request, entry_id: int):
    entry = get_object_or_404(ScrapEntry.objects.select_related("category"), pk=entry_id)
    error = ""
    form = {"weight": f"{entry.weight_lbs:f}".rstrip("0").rstrip("."),"description": entry.description, "note": entry.note,
            "category": entry.category.name if entry.category else "", "weighed_by": entry.weighed_by,
            "taken_in": timezone.localtime(entry.weighed_at).date().isoformat()}
    if request.method == "POST" and not _demo():
        who = _who(request)
        form = {key: request.POST.get(key, "").strip()
                for key in ("weight", "description", "note", "category", "weighed_by", "taken_in")}
        weight = _parse_lbs(form["weight"])
        taken_by = clean_name(form["weighed_by"])
        # An unchanged date keeps its time of day; a new one is read like on the weigh form.
        same_day = form["taken_in"] == timezone.localtime(entry.weighed_at).date().isoformat()
        taken_at = entry.weighed_at if same_day else (_taken_in(form["taken_in"]) if form["taken_in"] else None)
        if not who:
            error = "Add your name so everyone knows who changed it."
        elif weight is None:
            error = "Enter the weight in pounds, like 12.5."
        elif not taken_by:
            error = "Say who took it in."
        elif taken_at is None:
            error = "Pick the date it was taken in (today or earlier)."
        else:
            entry.weight_lbs = weight
            entry.weighed_by, entry.weighed_at = taken_by, taken_at
            entry.description = " ".join(form["description"].split())[:200]
            entry.note = " ".join(form["note"].split())[:255]
            entry.category = importer.category_for(form["category"], who)
            entry.edited_by, entry.edited_at = who, timezone.now()
            entry.save()
            messages.success(request, "Changes saved.")
            return _back(request)
    return render(request, "scrap/edit.html", {
        "page": "scrap", "entry": entry, "form": form, "error": error, "demo": _demo(),
        "ask_name": _asks_for_name(request), "categories": _visible_categories(),
        "today": timezone.localdate().isoformat(),
        "next":request.GET.get("next") or request.POST.get("next") or "",
    })


@require_POST
def unmove(request, entry_id: int):
    """Put an entry back to "waiting to move" (it was marked moved by mistake)."""
    entry = get_object_or_404(ScrapEntry, pk=entry_id)
    who = request.actor[:80]
    if _demo():
        messages.error(request, "Turned off in the demo.")
    elif entry.moved_at:
        entry.moved_at, entry.moved_by = None, ""
        entry.edited_by, entry.edited_at = who, timezone.now()
        entry.save()
        messages.success(request, "Put back to waiting to move.")
    return redirect("scrap_edit", entry_id=entry.pk)


@require_POST
def delete(request, entry_id: int):
    entry = get_object_or_404(ScrapEntry, pk=entry_id)
    if _demo():
        messages.error(request, "Deleting is turned off in the demo.")
        return redirect("scrap_edit", entry_id=entry.pk)
    if settings.PINKSHEET["REQUIRE_LOGIN"] and not request.user.is_staff:
        messages.error(request, "Only a staff member can delete scrap entries. Fix the weight instead, or ask staff.")
        return redirect("scrap_edit", entry_id=entry.pk)
    label = str(entry)
    entry.delete()
    messages.success(request, f"Deleted {label}.")
    return _back(request)


@require_GET
def scale_reading(request):
    """The live scale weight for the Take in form, which asks about once a second."""
    data = scale.reading()
    return json_ok(**{**data, "lbs": f"{data['lbs']:f}" if data["lbs"] is not None else None})


# ── Categories ───────────────────────────────────────────────────────────────
def categories(request):
    if request.method == "POST" and not _demo():
        action = request.POST.get("action")
        who = request.actor[:80]
        name = " ".join(request.POST.get("name", "").split())[:40]
        if action == "add":
            if not name:
                messages.error(request, "Type a name for the new category.")
            elif ScrapCategory.objects.filter(name__iexact=name, hidden=False).exists():
                messages.info(request, f"“{name}” is already a category.")
            else:
                importer.category_for(name, who)
                messages.success(request, f"Added “{name}”.")
        elif action in ("rename", "hide", "show") and request.POST.get("id", "").isdigit():
            category = get_object_or_404(ScrapCategory, pk=int(request.POST["id"]))
            if action == "rename":
                clash = ScrapCategory.objects.filter(name__iexact=name).exclude(pk=category.pk).exists()
                if not name:
                    messages.error(request, "A category needs a name.")
                elif clash:
                    messages.error(request, f"There is already a category called “{name}”.")
                else:
                    old, category.name = category.name, name
                    category.save(update_fields=["name"])
                    messages.success(request, f"Renamed “{old}” to “{name}”.")
            else:
                category.hidden = action == "hide"
                category.save(update_fields=["hidden"])
                messages.success(request, f"“{category.name}” is {'hidden from the list' if category.hidden else 'back in the list'}.")
        return redirect("scrap_categories")
    rows = ScrapCategory.objects.annotate(n=Count("entries"), lbs=Sum("entries__weight_lbs")).order_by("hidden", "name")
    return render(request, "scrap/categories.html", {"page": "scrap", "rows": rows, "demo": _demo()})


# ── Export ───────────────────────────────────────────────────────────────────
def export_csv(request):
    _, start, end, period_label = _period(request)
    entries, _ = _filtered_history(request, start, end)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    stamp = timezone.localtime().strftime("%Y-%m-%d")
    response["Content-Disposition"] = f'attachment; filename="scrap-{stamp}.csv"'
    response.write("﻿")  # Excel opens UTF-8 correctly with a BOM
    writer = csv.writer(response)
    writer.writerow(["Date taken in", "Taken in by", "Weight (lbs)", "What it is", "Category", "Moved to scrap",
                     "Moved by", "Note", "Weight from", "Entered by"])
    for entry in entries.order_by("weighed_at", "id").iterator():
        writer.writerow([
            timezone.localtime(entry.weighed_at).strftime("%Y-%m-%d %H:%M"), entry.weighed_by, f"{entry.weight_lbs}",
            entry.description, entry.category.name if entry.category else "",
            timezone.localtime(entry.moved_at).strftime("%Y-%m-%d %H:%M") if entry.moved_at else "",
            entry.moved_by, entry.note, entry.get_source_display(), entry.added_by,
        ])
    return response


# ── Import ───────────────────────────────────────────────────────────────────
def _mapping_from(request, headers: list[str]) -> dict[str, str]:
    return {name: request.POST.get(f"map_{name}", "") for name in importer.FIELDS
            if request.POST.get(f"map_{name}", "") in headers}


def import_view(request):
    stash = request.session.get(IMPORT_SESSION_KEY)
    importer_name = request.actor[:80]
    if request.method == "POST" and not _demo():
        action = request.POST.get("action") or "preview"  # changing a column choice re-submits without one
        if action == "cancel":
            request.session.pop(IMPORT_SESSION_KEY, None)
            return redirect("scrap_import")
        if action == "upload":
            upload = request.FILES.get("file")
            if not upload:
                messages.error(request, "Pick a CSV file first.")
                return redirect("scrap_import")
            if upload.size > IMPORT_MAX_BYTES:
                messages.error(request, "That file is too large (10 MB at most).")
                return redirect("scrap_import")
            suffix = Path(upload.name).suffix.lower() or ".csv"
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / f"upload{suffix}"
                with path.open("wb") as out:
                    for chunk in upload.chunks():
                        out.write(chunk)
                try:
                    headers, rows = importer.read(path, upload.name)
                except SheetError as exc:
                    messages.error(request, str(exc))
                    return redirect("scrap_import")
            request.session[IMPORT_SESSION_KEY] = {
                "name": upload.name[:120], "headers": headers, "rows": rows,
                "mapping": importer.guess_mapping(headers), "moved_default": True,
            }
            return redirect("scrap_import")
        if stash and action in ("preview", "import"):
            stash["mapping"] = _mapping_from(request, stash["headers"])
            stash["moved_default"] = request.POST.get("moved_default", "1") == "1"
            request.session[IMPORT_SESSION_KEY] = stash
            if action == "import":
                try:
                    result = importer.plan(stash["rows"], stash["mapping"], moved_default=stash["moved_default"],
                                           importer=importer_name)
                except SheetError as exc:
                    messages.error(request, str(exc))
                    return redirect("scrap_import")
                added = importer.apply(result, importer=importer_name)
                request.session.pop(IMPORT_SESSION_KEY, None)
                summary = f"Imported {added} row{'s' if added != 1 else ''} ({lbs(result.new_lbs)} lbs) from {stash['name']}."
                if result.duplicates:
                    summary += f" {result.duplicates} were already imported and were skipped."
                if result.skipped:
                    summary += f" {len(result.skipped)} rows had no usable weight or date and were left out."
                messages.success(request, summary)
                return redirect("scrap")
            return redirect("scrap_import")
    context = {"page": "scrap", "demo": _demo(), "stash": stash, "fields": importer.FIELDS}
    if stash:
        try:
            result = importer.plan(stash["rows"], stash["mapping"], moved_default=stash["moved_default"],
                                   importer=importer_name)
            context["error"] = ""
        except SheetError as exc:
            result, context["error"] = None, str(exc)
        context.update({
            "result": result, "preview": result.rows[:15] if result else [],
            "field_rows": [(name, label, stash["mapping"].get(name, "")) for name, (label, _) in importer.FIELDS.items()],
        })
    return render(request, "scrap/import.html", context)
