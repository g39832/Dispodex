from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect

# Paths that must keep working even when maintenance mode or login is on.
_ALWAYS_OPEN_PREFIXES = ("/static/", "/webhooks/", "/square_webhook.php", "/api/health")
# The imaging app signs in with its own API key instead.
_LOGIN_EXEMPT_PREFIXES = _ALWAYS_OPEN_PREFIXES + ("/login/", "/logout/", "/admin/", "/api/imaging/report/")


class SecurityHeadersMiddleware:
    """Content-Security-Policy and Permissions-Policy for every page."""

    CSP = "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self' 'unsafe-inline'",
            # https: lets the listing-image composer show pasted eBay image URLs.
            "img-src 'self' data: blob: https:",
            "font-src 'self'",
            # QZ Tray (label printer bridge) listens on localhost websockets.
            "connect-src 'self' ws://localhost:* wss://localhost:* ws://127.0.0.1:* wss://127.0.0.1:*",
            "frame-ancestors 'self'",
            "form-action 'self'",
            "base-uri 'self'",
        ]
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not request.path.startswith("/admin/"):
            response.setdefault("Content-Security-Policy", self.CSP)
        # camera=(self) so the board's QR scanner and the phone page can use the camera.
        response.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
        return response


class MaintenanceModeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        config = settings.PINKSHEET
        if config["MAINTENANCE_MODE"] and not request.path.startswith(_ALWAYS_OPEN_PREFIXES):
            message = config["MAINTENANCE_MESSAGE"]
            if request.path.startswith("/api/"):
                return JsonResponse({"ok": False, "error": message, "maintenance": True}, status=503)
            return HttpResponse(message, status=503, content_type="text/plain; charset=utf-8")
        return self.get_response(request)


class LoginRequiredMiddleware:
    """Optional sign-in wall, switched on with PINKSHEET_REQUIRE_LOGIN=1."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            settings.PINKSHEET["REQUIRE_LOGIN"]
            and not request.user.is_authenticated
            and not request.path.startswith(_LOGIN_EXEMPT_PREFIXES)
        ):
            if request.path.startswith("/api/"):
                return JsonResponse({"ok": False, "error": "Please sign in."}, status=401)
            return redirect(f"{settings.LOGIN_URL}?next={request.get_full_path()}")
        return self.get_response(request)
