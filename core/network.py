"""Restrict operator-only actions (backups, full Square sync…) to the local network."""
import ipaddress
from functools import wraps

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
        if not is_private_request(request):
            return json_error("This action is only available on the local network.", 403)
        return view(request, *args, **kwargs)

    return wrapper
