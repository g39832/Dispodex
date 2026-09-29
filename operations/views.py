"""Backups, health, the System page, QZ Tray signing, and redirects for old PHP links."""
import base64
import tempfile
from pathlib import Path
from urllib.parse import urlencode

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.conf import settings
from django.contrib.auth import views as auth_views
from django.http import Http404, HttpResponse, HttpResponsePermanentRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.http import json_error, json_ok, read_json_body
from core.network import private_network_only
from operations import backups, db_import, health
from operations.models import SystemState
from squaresync.config import get_config
from squaresync.models import ReconciliationRun, SyncAuditLog, SyncJob, WebhookEvent


# ── backups ──────────────────────────────────────────────────────────────────
@require_POST
@private_network_only
def backup_now(request):
    result = backups.run_backup()
    if not result.ok:
        return json_error(result.error or "Backup failed.", 500, messages=result.messages)
    return json_ok(file=result.path.name if result.path else None, messages=result.messages)


@require_POST
@private_network_only
def verify_backup(request):
    outcome = backups.verify_latest()
    if not outcome["ok"]:
        return json_error("; ".join(outcome["messages"]), 500, **outcome)
    return json_ok(**{k: v for k, v in outcome.items() if k != "ok"})


@require_POST
@private_network_only
def import_database(request):
    upload = request.FILES.get("file")
    if upload is None:
        return json_error("Choose a database file to import.")
    with tempfile.TemporaryDirectory(prefix="dispodex-upload-") as tmp:
        path = Path(tmp) / "upload.sqlite3"
        with path.open("wb") as handle:
            for chunk in upload.chunks():
                handle.write(chunk)
        try:
            outcome = db_import.import_database(path, original_name=upload.name, actor=request.actor)
        except db_import.ImportRefused as exc:
            return json_error(str(exc))
    return json_ok(kind=outcome.kind, items=outcome.items, backup=outcome.backup, messages=outcome.messages)


@require_GET
def health_json(request):
    return json_ok(**health.quick_status())


def system(request):
    report = health.full_report()
    config = get_config()
    return render(
        request,
        "operations/system.html",
        {
            "page": "system",
            "app_folder": str(settings.BASE_DIR),
            "report": report,
            "square": config,
            "jobs": SyncJob.objects.exclude(status=SyncJob.State.COMPLETED).order_by("-updated_at")[:50],
            "audit": SyncAuditLog.objects.all()[:40],
            "webhooks": WebhookEvent.objects.all()[:20],
            "runs": ReconciliationRun.objects.all()[:10],
            "backups": [
                {"name": p.name, "size": p.stat().st_size, "modified": p.stat().st_mtime}
                for p in backups.list_backups()[:15]
            ],
            "suggested_webhook_url": request.build_absolute_uri(reverse("square_webhook")).replace("http://", "https://"),
            "backup_hour": settings.PINKSHEET["BACKUP_HOUR"],
            "backup_path": settings.BACKUP_DIR,
            "last_import": SystemState.get(db_import.LAST_IMPORT_KEY),
        },
    )


# ── QZ Tray label printing (certificate + request signing) ───────────────────
def _qz_file(name: str):
    path = settings.PINKSHEET["QZ_SIGNING_DIR"] / name
    return path if path.exists() else None


def _origin_allowed(request) -> bool:
    origin = request.headers.get("Origin", "")
    allowed = settings.PINKSHEET["QZ_ALLOWED_ORIGINS"]
    if not origin:
        return True  # same-origin fetches may omit Origin on GET
    if allowed:
        return origin in allowed
    return origin == f"{request.scheme}://{request.get_host()}"


@require_GET
def qz_certificate(request):
    cert = _qz_file("digital-certificate.txt")
    if cert is None:
        return HttpResponse(
            "QZ signing is not configured. Save the certificate from QZ Tray (Advanced > Site Manager) "
            "as data/qz-signing/digital-certificate.txt.",
            status=404, content_type="text/plain",
        )
    response = HttpResponse(cert.read_text(encoding="utf-8"), content_type="text/plain")
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt  # QZ signing requests carry no user data; origin is checked instead
@require_POST
def qz_sign(request):
    if not _origin_allowed(request):
        return HttpResponse("Origin not allowed.", status=403, content_type="text/plain")
    key_file = _qz_file("private-key.pem")
    if key_file is None:
        return HttpResponse("QZ signing is not configured.", status=503, content_type="text/plain")
    body = read_json_body(request)
    to_sign = body.get("request")
    if not isinstance(to_sign, str) or not to_sign:
        return HttpResponse('The "request" field is required.', status=400, content_type="text/plain")
    if len(to_sign) > 64 * 1024:
        return HttpResponse("Signing request is too large.", status=400, content_type="text/plain")
    key = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
    signature = key.sign(to_sign.encode("utf-8"), padding.PKCS1v15(), hashes.SHA512())
    response = HttpResponse(base64.b64encode(signature).decode(), content_type="text/plain")
    response["Cache-Control"] = "no-store"
    return response


# ── sign in / out (only used when PINKSHEET_REQUIRE_LOGIN=1) ─────────────────
class LoginView(auth_views.LoginView):
    template_name = "operations/login.html"


# ── links from the old PHP app (printed QR codes, bookmarks) ─────────────────
LEGACY_PAGES = {
    "index.php": "intake",
    "intake.php": "intake",
    "home.php": "dashboard",
    "lookup.php": "lookup",
    "kanban.php": "board",
    "archive.php": "archive",
    "prompt_builder.php": "scripts",
    "ebay_images.php": "listing_images",
}


def legacy_redirect(request, page: str):
    params = request.GET.copy()
    if page == "card.php" or page == "mobile_action.php":
        sku = params.get("sku", "")
        if not sku:
            raise Http404("Missing SKU")
        name = "card" if page == "card.php" else "mobile_card"
        return HttpResponsePermanentRedirect(reverse(name, args=[sku]))
    if page == "photo.php":
        photo_id = params.get("id", "")
        if not photo_id.isdigit():
            raise Http404("Missing photo id")
        extra = {k: v for k, v in params.items() if k in ("thumb", "download")}
        url = reverse("photo", args=[int(photo_id)])
        return HttpResponsePermanentRedirect(url + ("?" + urlencode(extra) if extra else ""))
    target = LEGACY_PAGES.get(page)
    if target is None:
        raise Http404("Unknown page")
    if "clear_draft" in params:
        params.pop("clear_draft")
        params["new"] = "1"
    query = params.urlencode()
    return HttpResponsePermanentRedirect(reverse(target) + (f"?{query}" if query else ""))
