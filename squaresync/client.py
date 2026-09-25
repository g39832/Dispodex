"""Minimal Square REST client with retries, timeouts and structured logging."""
from __future__ import annotations

import json
import logging
import random
import time
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import requests

from squaresync.config import SquareConfig

logger = logging.getLogger("pinksheet.square")

TRANSIENT_STATUS = {429, 500, 502, 503, 504}


class SquareError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def log_event(**event: Any) -> None:
    """One JSON line per event in data/logs/square_sync.log."""
    clean = {k: v for k, v in event.items() if v not in (None, "")}
    clean.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    logger.info(json.dumps(clean, default=str))


def _error_summary(decoded: Any, raw: str) -> str:
    if isinstance(decoded, dict) and isinstance(decoded.get("errors"), list):
        parts = []
        for error in decoded["errors"]:
            if isinstance(error, dict):
                parts.append(f"{error.get('category', '')} {error.get('code', '')}: {error.get('detail', '')}".strip())
        if parts:
            return "; ".join(parts)
    return raw[:500]


def _retry_after_seconds(response: requests.Response | None) -> float | None:
    if response is None:
        return None
    value = response.headers.get("Retry-After")
    if not value:
        return None
    if value.strip().isdigit():
        return float(value.strip())
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


class SquareClient:
    def __init__(self, config: SquareConfig, session: requests.Session | None = None, sleep=time.sleep):
        self.config = config
        self.session = session or requests.Session()
        self._sleep = sleep
        self.requests_made = 0

    def _headers(self) -> dict[str, str]:
        return {
            "Square-Version": self.config.api_version,
            "Authorization": f"Bearer {self.config.token}",
            "Accept": "application/json",
        }

    def request(self, method: str, path: str, json_body: dict | None = None, files=None, data=None) -> dict:
        url = self.config.base_url + path
        attempts = self.config.max_retries + 1
        last_error = "Square request failed"
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            response = None
            self.requests_made += 1
            try:
                response = self.session.request(
                    method,
                    url,
                    headers=self._headers(),
                    json=json_body,
                    files=files,
                    data=data,
                    timeout=(self.config.connect_timeout, self.config.timeout),
                )
            except requests.RequestException as exc:
                last_error = f"Square request failed: {exc}"
                log_event(operation="square_api", method=method, path=urlparse(url).path,
                          status="retryable_transport_error", attempt=attempt,
                          duration_ms=int((time.monotonic() - started) * 1000), message=last_error)
            else:
                raw = response.text
                try:
                    decoded = response.json()
                except ValueError:
                    decoded = None
                duration = int((time.monotonic() - started) * 1000)
                if 200 <= response.status_code < 300 and isinstance(decoded, dict):
                    log_event(operation="square_api", method=method, path=urlparse(url).path,
                              http_status=response.status_code, status="success", attempt=attempt, duration_ms=duration)
                    return decoded
                if decoded is None:
                    last_error = f"Square returned a non-JSON response with status {response.status_code}"
                else:
                    last_error = f"Square API error {response.status_code}: {_error_summary(decoded, raw)}"
                transient = response.status_code in TRANSIENT_STATUS
                log_event(operation="square_api", method=method, path=urlparse(url).path,
                          http_status=response.status_code,
                          status="retryable_http_error" if transient else "failure",
                          attempt=attempt, duration_ms=duration, message=last_error)
                if not transient:
                    raise SquareError(last_error, response.status_code)
                if files is not None:
                    for value in files.values():
                        handle = value[1] if isinstance(value, tuple) else value
                        if hasattr(handle, "seek"):
                            handle.seek(0)
            if attempt < attempts:
                delay = _retry_after_seconds(response)
                if delay is None:
                    delay = min(10.0, 0.25 * (2 ** (attempt - 1)) + random.uniform(0, 0.25))
                self._sleep(min(30.0, delay))
        raise SquareError(last_error)

    # Convenience wrappers
    def get(self, path: str) -> dict:
        return self.request("GET", path)

    def post(self, path: str, body: dict) -> dict:
        return self.request("POST", path, json_body=body)
