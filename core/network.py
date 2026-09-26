"""Restrict operator-only actions (backups, full Square sync…) to the local network."""
import ipaddress
from functools import wraps

from django.conf import settings

from core.http import json_error


def is_private_request(request) -> bool:
    remote = (request.META.get("REMOTE_ADDR") or "").strip()
    if not remote:
        return False
    try:
        address = ipaddress.ip_address(remote)
    except ValueError:
        return False
    return address.is_private or address.is_loopback


def private_network_only(view):
    """Reject requests that did not come from localhost or a private LAN address."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        # Behind the demo's tunnel every visitor looks local, so the address check can't be trusted.
        if settings.PINKSHEET["DEMO_MODE"]:
            return json_error("This action is turned off in the demo.", 403)
        if not is_private_request(request):
            return json_error("This action is only available on the local network.", 403)
        return view(request, *args, **kwargs)

    return wrapper


def not_in_demo(view):
    """Reject the request in demo mode (e.g. uploads, which strangers could abuse)."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if settings.PINKSHEET["DEMO_MODE"]:
            return json_error("Uploads are turned off in the demo.", 403)
        return view(request, *args, **kwargs)

    return wrapper
