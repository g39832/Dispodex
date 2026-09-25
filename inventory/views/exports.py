"""Download endpoints for the inventory exports."""
from django.http import FileResponse, HttpResponse

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
