"""eBay category list for the intake form's category picker.

Uses the live snapshot at ``data/ebay_categories.json`` when one has been
downloaded (``python manage.py refresh_ebay_categories``), otherwise the list
bundled with the app.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path

import requests
from django.conf import settings

BUNDLED_PATH = Path(__file__).resolve().parent.parent / "data" / "ebay_categories_bundled.json"
TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
TAXONOMY_URL = "https://api.ebay.com/commerce/taxonomy/v1/category_tree/"
DEFAULT_TOPS = [
    "Computers/Tablets & Networking",
    "Consumer Electronics",
    "Cell Phones, Smart Watches & Accessories",
    "Video Games & Consoles",
]


def snapshot_path() -> Path:
    return Path(settings.DATA_DIR) / "ebay_categories.json"


def normalize_list(entries) -> list[dict]:
    """Drop blanks and duplicates (by path, else by name), keep order."""
    out, seen = [], set()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        path = str(entry.get("path") or "").strip()
        key = (path or name).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"id": str(entry.get("id") or "").strip(), "name": name, "path": path})
    return out


def bundled() -> list[dict]:
    return normalize_list(json.loads(BUNDLED_PATH.read_text(encoding="utf-8")))


def category_list() -> dict:
    path = snapshot_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            categories = normalize_list(data.get("categories"))
            if categories:
                return {
                    "categories": categories,
                    "generated_at": str(data.get("generated_at") or ""),
                    "source": str(data.get("source") or "snapshot"),
                }
        except (ValueError, OSError, AttributeError):
            pass
    return {"categories": bundled(), "generated_at": "", "source": "bundled"}


def _flatten(node: dict, path: list[str], out: list[dict]) -> None:
    category = node.get("category") or {}
    name = str(category.get("categoryName") or "").strip()
    current = path + [name] if name else path
    children = node.get("childCategoryTreeNodes") or []
    if (node.get("leafCategoryTreeNode") or not children) and name and len(current) >= 2:
        out.append({"id": str(category.get("categoryId") or ""), "name": name, "path": " > ".join(current)})
        return
    for child in children:
        if isinstance(child, dict):
            _flatten(child, current, out)


def refresh_from_ebay(top_names: list[str] | None = None) -> int:
    """Download eBay's live category tree and save the electronics leaves. Returns the count."""
    config = settings.PINKSHEET
    client_id, secret = config["EBAY_CLIENT_ID"], config["EBAY_CLIENT_SECRET"]
    if not client_id or not secret or client_id.startswith("replace-with-"):
        raise RuntimeError("Set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET in .env first.")
    marketplace = config["EBAY_MARKETPLACE_ID"]
    auth = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    token_resp = requests.post(
        TOKEN_URL,
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"},
        timeout=60,
    )
    token_resp.raise_for_status()
    token = token_resp.json().get("access_token")
    if not token:
        raise RuntimeError("eBay did not return an access token.")
    tree_resp = requests.get(
        TAXONOMY_URL + marketplace,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=120,
    )
    tree_resp.raise_for_status()
    tree = tree_resp.json()
    tops = set(top_names or DEFAULT_TOPS)
    leaves: list[dict] = []
    for node in (tree.get("rootCategoryNode") or {}).get("childCategoryTreeNodes") or []:
        name = str((node.get("category") or {}).get("categoryName") or "")
        if name in tops:
            _flatten(node, [], leaves)
    if not leaves:
        raise RuntimeError("No matching electronics categories were found in eBay's tree.")
    # Keep the hand-picked categories that live outside the electronics tops.
    for entry in bundled():
        if entry["path"].split(" > ", 1)[0] not in tops:
            leaves.append(entry)
    categories = normalize_list(leaves)
    snapshot_path().write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "source": "ebay",
                "marketplace_id": marketplace,
                "tree_version": str(tree.get("categoryTreeVersion") or ""),
                "categories": categories,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return len(categories)
