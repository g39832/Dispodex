"""Read the shop scale through its scale agent (a small web service next to the scale).

The agent answers GET <PINKSHEET_SCALE_URL> with
    {"online": true, "stable": true, "weight": 127.0, "unit": "lb", "raw": "ST,GS,+    127lb"}
Dispodex asks it on the page's behalf, so phones and HTTPS pages never talk to
the scale directly, and turns any unit into pounds.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings

logger = logging.getLogger("pinksheet")

TIMEOUT_SECONDS = 2
TO_LBS = {
    "lb": Decimal("1"), "lbs": Decimal("1"), "pound": Decimal("1"), "pounds": Decimal("1"),
    "kg": Decimal("2.20462262"), "kgs": Decimal("2.20462262"),
    "g": Decimal("0.00220462262"), "oz": Decimal("0.0625"),
}


def url() -> str:
    return settings.PINKSHEET["SCALE_URL"]


def reading() -> dict:
    """{"configured", "online", "stable", "lbs", "error"} — never raises."""
    if not url():
        return {"configured": False, "online": False, "stable": False, "lbs": None,
                "error": "No scale is set up. Add PINKSHEET_SCALE_URL to .env."}
    try:
        response = requests.get(url(), timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.info("Scale not reachable at %s: %s", url(), exc)
        return {"configured": True, "online": False, "stable": False, "lbs": None,
                "error": "Can't reach the scale. Check that it's on and its agent is running."}
    if not isinstance(data, dict) or not data.get("online"):
        return {"configured": True, "online": False, "stable": False, "lbs": None,
                "error": "The scale agent is running but the scale isn't connected."}
    factor = TO_LBS.get(str(data.get("unit") or "lb").strip().lower())
    try:
        weight = Decimal(str(data.get("weight")))
    except (InvalidOperation, ValueError):
        weight = None
    if factor is None or weight is None or not weight.is_finite():
        return {"configured": True, "online": True, "stable": False, "lbs": None,
                "error": f"The scale sent a reading Dispodex can't use ({data.get('raw') or data.get('weight')})."}
    lbs = (weight * factor).quantize(Decimal("0.01"))
    return {"configured": True, "online": True, "stable": bool(data.get("stable")), "lbs": lbs, "error": ""}
