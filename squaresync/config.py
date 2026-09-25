"""Square settings, read from .env through ``settings.SQUARE``.

To turn Square on, fill in these lines in ``.env`` and restart Dispodex:

    SQUARE_ENVIRONMENT=production        # or sandbox while testing
    SQUARE_ACCESS_TOKEN=...
    SQUARE_LOCATION_ID=...
    SQUARE_WEBHOOK_SIGNATURE_KEY=...     # only needed for sales coming back from Square
    SQUARE_WEBHOOK_NOTIFICATION_URL=https://your-public-address/webhooks/square/

``python manage.py configure_square`` asks for these and writes them for you.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

PLACEHOLDER_PREFIX = "replace-with-"


def _real(value: str) -> bool:
    return bool(value) and not value.startswith(PLACEHOLDER_PREFIX)


@dataclass(frozen=True)
class SquareConfig:
    environment: str
    token: str
    location_id: str
    api_version: str
    currency: str
    default_quantity: int
    max_retries: int
    timeout: int
    connect_timeout: int
    sync_switch: bool
    webhook_signature_key: str
    webhook_notification_url: str
    webhook_max_age_seconds: int
    webhook_max_body_bytes: int

    @property
    def base_url(self) -> str:
        if self.environment == "production":
            return "https://connect.squareup.com"
        return "https://connect.squareupsandbox.com"

    @property
    def has_credentials(self) -> bool:
        return _real(self.token) and _real(self.location_id)

    @property
    def enabled(self) -> bool:
        """True only when sync is switched on AND real credentials are present."""
        return self.sync_switch and self.has_credentials

    @property
    def webhooks_enabled(self) -> bool:
        return _real(self.webhook_signature_key) and bool(self.webhook_notification_url)

    def missing(self) -> list[str]:
        needed = []
        if not _real(self.token):
            needed.append("SQUARE_ACCESS_TOKEN")
        if not _real(self.location_id):
            needed.append("SQUARE_LOCATION_ID")
        return needed

    def missing_webhook(self) -> list[str]:
        needed = []
        if not _real(self.webhook_signature_key):
            needed.append("SQUARE_WEBHOOK_SIGNATURE_KEY")
        if not self.webhook_notification_url:
            needed.append("SQUARE_WEBHOOK_NOTIFICATION_URL")
        return needed


def get_config() -> SquareConfig:
    s = settings.SQUARE
    return SquareConfig(
        environment=s["ENVIRONMENT"],
        token=s["ACCESS_TOKEN"],
        location_id=s["LOCATION_ID"],
        api_version=s["API_VERSION"],
        currency=s["CURRENCY"],
        default_quantity=max(0, s["DEFAULT_QUANTITY"]),
        max_retries=s["MAX_RETRIES"],
        timeout=s["TIMEOUT_SECONDS"],
        connect_timeout=s["CONNECT_TIMEOUT_SECONDS"],
        sync_switch=s["SYNC_ENABLED"],
        webhook_signature_key=s["WEBHOOK_SIGNATURE_KEY"],
        webhook_notification_url=s["WEBHOOK_NOTIFICATION_URL"],
        webhook_max_age_seconds=s["WEBHOOK_MAX_AGE_SECONDS"],
        webhook_max_body_bytes=s["WEBHOOK_MAX_BODY_BYTES"],
    )
