"""Download endpoints for the inventory exports."""
from django.http import FileResponse, HttpResponse

from inventory.models import Status
from inventory.services import exports
from inventory.services.search import ItemFilters, filter_items


def _items(request):
    filters = ItemFilters.from_query(request.GET)
    return list(filter_items(filters)), filters.scope


def inventory_csv(request):
    items, scope = _items(request)
    response = HttpResponse(exports.inventory_csv(items), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{exports.filename("inventory", scope, "csv")}"'
    response["Cache-Control"] = "no-store"
    return response


def _busy() -> HttpResponse:
    return HttpResponse(
        "Another photo export is still being prepared. Try again in a minute.",
        status=429, content_type="text/plain; charset=utf-8",
    )


def inventory_zip(request):
    if not exports.heavy_export_lock.acquire(blocking=False):
        return _busy()
    try:
        items, scope = _items(request)
        handle = exports.inventory_zip(items, exports.filename("inventory", scope, "csv"))
    finally:
        exports.heavy_export_lock.release()
    return FileResponse(handle, as_attachment=True, filename=exports.filename("inventory", scope, "zip"),
                        content_type="application/zip")


def inventory_xlsx(request):
    if not exports.heavy_export_lock.acquire(blocking=False):
        return _busy()
    try:
        items, scope = _items(request)
        handle = exports.inventory_xlsx(items)
    finally:
        exports.heavy_export_lock.release()
    return FileResponse(
        handle, as_attachment=True, filename=exports.filename("inventory", scope, "xlsx"),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ── Partner exports: same filters, never sold items, only partner-safe columns ──
def _partner_items(request):
    items = filter_items(ItemFilters.from_query(request.GET)).exclude(status=Status.SOLD)
    return list(items)


def _partner_name(ext: str) -> str:
    return exports.filename("inventory_partner", "all", ext)


def partner_csv(request):
    csv_text = exports.inventory_csv(_partner_items(request), exports.PARTNER_COLUMNS)
    response = HttpResponse(csv_text, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{_partner_name("csv")}"'
    response["Cache-Control"] = "no-store"
    return response


def partner_zip(request):
    if not exports.heavy_export_lock.acquire(blocking=False):
        return _busy()
    try:
        handle = exports.inventory_zip(_partner_items(request), _partner_name("csv"), exports.PARTNER_COLUMNS)
    finally:
        exports.heavy_export_lock.release()
    return FileResponse(handle, as_attachment=True, filename=_partner_name("zip"), content_type="application/zip")


def partner_xlsx(request):
    if not exports.heavy_export_lock.acquire(blocking=False):
        return _busy()
    try:
        handle = exports.inventory_xlsx(_partner_items(request), exports.PARTNER_COLUMNS)
    finally:
        exports.heavy_export_lock.release()
    return FileResponse(
        handle, as_attachment=True, filename=_partner_name("xlsx"),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def partner_sortable_xlsx(request):
    handle = exports.sortable_xlsx(_partner_items(request), exports.PARTNER_COLUMNS)
    return FileResponse(
        handle, as_attachment=True, filename=exports.filename("inventory_partner_sortable", "all", "xlsx"),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
