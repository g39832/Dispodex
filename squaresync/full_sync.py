"""The dashboard's "Sync Square now": push every SKU in a background thread, with progress.

Only one full sync runs at a time. Its progress lives here (one Dispodex process
serves everyone), so every open dashboard can show "Syncing… 640 of 1,439" and a
second click gets the running sync's progress instead of starting another.
"""
from __future__ import annotations

import logging
import threading

from django.db import close_old_connections
from django.utils import timezone

from inventory.models import Item
from squaresync import sync as square_sync

logger = logging.getLogger("pinksheet.square")

_guard = threading.Lock()
_thread: threading.Thread | None = None
_state: dict = {"running": False}


def progress() -> dict:
    """A copy of the current (or last finished) full sync's progress."""
    with _guard:
        state = dict(_state)
        state["errors"] = list(state.get("errors", []))[:20]
        return state


def start(config, started_by: str) -> tuple[bool, dict]:
    """Start a full sync unless one is running. Returns (started, progress)."""
    global _thread
    with _guard:
        if _state.get("running"):
            return False, {**_state, "errors": list(_state.get("errors", []))[:20]}
        skus = list(Item.objects.exclude(sku_normalized="").values_list("sku_normalized", flat=True))
        _state.clear()
        # "updated", not "ok": every JSON reply already has an "ok" flag.
        _state.update(running=True, total=len(skus), done=0, updated=0, skipped=0, error=0, errors=[],
                      started_by=started_by, started_at=timezone.localtime().strftime("%Y-%m-%d %H:%M"),
                      finished_at=None)
        _thread = threading.Thread(target=_run, args=(skus, config), name="square-full-sync", daemon=True)
        _thread.start()
        return True, {**_state, "errors": []}


def _run(skus: list[str], config) -> None:
    try:
        for sku in skus:
            try:
                result = square_sync.sync_item(sku, config=config)
                status, message = result.status, result.message
            except Exception as exc:  # noqa: BLE001 — one bad SKU must not stop the rest
                logger.exception("Full Square sync failed on %s", sku)
                status, message = "error", f"Unexpected error: {exc}"
            key = "updated" if status == "ok" else ("skipped" if status in ("skipped", "disabled") else "error")
            with _guard:
                _state["done"] += 1
                _state[key] += 1
                if key == "error":
                    _state["errors"].append({"sku": sku, "message": message})
    finally:
        with _guard:
            _state["running"] = False
            _state["finished_at"] = timezone.localtime().strftime("%Y-%m-%d %H:%M")
            summary = {k: _state[k] for k in ("total", "updated", "skipped", "error")}
        logger.info("Full Square sync finished: %s", summary)
        close_old_connections()


def wait(timeout: float | None = None) -> None:
    """Block until the running full sync finishes (used by tests)."""
    thread = _thread
    if thread is not None:
        thread.join(timeout)
