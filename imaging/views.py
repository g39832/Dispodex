"""The Imaging page, and the address the imaging app sends its JSON to."""
import hmac
import json

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.http import json_error, json_ok
from core.network import private_network_only
from core.skus import normalize_sku
from imaging import ingest
from imaging.models import DeviceReport
from inventory.models import Item

MAX_UPLOAD_BYTES = 1024 * 1024


_LABELS = {"cpu": "CPU", "ram_gb": "RAM (GB)", "disk_gb": "Disk (GB)", "ip_address": "IP address",
           "mac_address": "MAC address", "tpm": "TPM", "os": "OS", "battery_health_pct": "Battery health (%)"}


def _label(key: str) -> str:
    """"disk_serial" -> "Disk serial" for the All details list."""
    return _LABELS.get(key, str(key).replace("_", " ").strip().capitalize())


def _sent_key(request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("X-Api-Key", "").strip()


def _payloads(raw: bytes):
    """One JSON object, or a list of them."""
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ingest.ReportError("That isn't valid JSON.") from exc
    items = data if isinstance(data, list) else [data]
    if not items or len(items) > 100:
        raise ingest.ReportError("Send between 1 and 100 computers at a time.")
    return items


# ── The imaging app sends its JSON here ──────────────────────────────────────
@csrf_exempt
@require_POST
@private_network_only
def report_api(request):
    key = settings.PINKSHEET["IMAGING_API_KEY"]
    if not key:
        return json_error("Imaging reports are turned off. Set PINKSHEET_IMAGING_API_KEY in .env first.", 503)
    if not hmac.compare_digest(_sent_key(request).encode(), key.encode()):
        return json_error("Wrong or missing API key.", 401)
    if len(request.body) > MAX_UPLOAD_BYTES:
        return json_error("That JSON is too large.", 413)
    try:
        reports = [ingest.receive(data, reported_by=f"Imaging app {request.META.get('REMOTE_ADDR', '')}".strip())
                   for data in _payloads(request.body)]
    except ingest.ReportError as exc:
        return json_error(str(exc), 400)
    results = [{"serial": r.serial, "item": r.item.sku if r.item else None} for r in reports]
    return json_ok(received=len(results), computers=results)


# ── The Imaging page ────────────────────────────────────────────────────────
def imaging(request):
    query = request.GET.get("q", "").strip()
    reports = DeviceReport.objects.select_related("item")
    if query:
        reports = reports.filter(
            Q(serial__icontains=query) | Q(manufacturer__icontains=query) | Q(model__icontains=query)
            | Q(item__sku__icontains=query) | Q(stage__icontains=query) | Q(status__icontains=query)
        )
    page_obj = Paginator(reports, 25).get_page(request.GET.get("page"))
    ingest.link_matches(page_obj)
    for report in page_obj:
        report.specs = ingest.item_specs(report.payload)
        report.details = sorted(
            (_label(key), value) for key, value in report.payload.items()
            if not isinstance(value, (dict, list)) and value not in (None, "")
        )
    context = {
        "page": "imaging", "page_obj": page_obj, "query": query, "total": DeviceReport.objects.count(),
        "api_ready": bool(settings.PINKSHEET["IMAGING_API_KEY"]),
        "api_url": request.build_absolute_uri(reverse("api_imaging_report")),
        "demo": settings.PINKSHEET["DEMO_MODE"],
    }
    if request.headers.get("X-Refresh") == "1":
        return render(request, "imaging/_list.html", context)
    return render(request, "imaging/imaging.html", context)


@require_POST
def upload(request):
    if settings.PINKSHEET["DEMO_MODE"]:
        messages.error(request, "Uploads are turned off in the demo.")
        return redirect("imaging")
    files = request.FILES.getlist("files")
    if not files:
        messages.error(request, "Pick one or more .json files first.")
        return redirect("imaging")
    saved, problems = 0, []
    for upload_file in files:
        try:
            if upload_file.size > MAX_UPLOAD_BYTES:
                raise ingest.ReportError("is too large.")
            for data in _payloads(upload_file.read()):
                ingest.receive(data, reported_by=request.actor)
                saved += 1
        except ingest.ReportError as exc:
            problems.append(f"{upload_file.name}: {exc}")
    if saved:
        messages.success(request, f"Added {saved} computer{'s' if saved != 1 else ''}.")
    for problem in problems:
        messages.error(request, problem)
    return redirect("imaging")


@require_POST
def link(request, report_id: int):
    report = get_object_or_404(DeviceReport, pk=report_id)
    sku = normalize_sku(request.POST.get("sku"))
    item = Item.objects.filter(sku_normalized=sku).first() if sku else None
    if item is None:
        messages.error(request, f"No item with SKU {sku}." if sku else "Type the item's SKU to link it.")
        return redirect("imaging")
    try:
        filled = ingest.link(report, item)
    except ingest.ReportError as exc:
        messages.error(request, str(exc))
        return redirect("imaging")
    detail = f" and filled in {len(filled)} blank field{'s' if len(filled) != 1 else ''}" if filled else ""
    messages.success(request, f"Linked {report.serial} to {item.sku}{detail}.")
    return redirect("imaging")


@require_POST
def unlink(request, report_id: int):
    report = get_object_or_404(DeviceReport, pk=report_id)
    report.item = None
    report.save(update_fields=["item"])
    messages.success(request, f"Unlinked {report.serial}. The item's fields were left as they are.")
    return redirect("imaging")


@require_POST
def remove(request, report_id: int):
    report = get_object_or_404(DeviceReport, pk=report_id)
    report.delete()
    messages.success(request, f"Removed {report.serial} from Imaging. Items were not changed.")
    return redirect("imaging")
