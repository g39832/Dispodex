"""JSON response helpers.

Every AJAX endpoint answers with the same shape so the front-end can always
check ``data.ok``:

    success: {"ok": true, ...fields}
    failure: {"ok": false, "error": "what went wrong"}
"""
import json
import logging

from django.http import JsonResponse

logger = logging.getLogger("pinksheet")


def json_ok(**data) -> JsonResponse:
    return JsonResponse({"ok": True, **data})


def json_error(message: str, status: int = 400, **data) -> JsonResponse:
    if status >= 500:
        logger.error("API error %s: %s", status, message)
    return JsonResponse({"ok": False, "error": message, **data}, status=status)


def read_json_body(request) -> dict:
    """Parse a JSON request body; returns {} for empty or invalid input."""
    if not request.body:
        return {}
    try:
        data = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}
