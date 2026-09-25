"""Ask for the Square details and write them into .env — no editing files by hand."""
import getpass
import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

FIELDS = [
    ("SQUARE_ENVIRONMENT", "Environment — type production for the real store, or sandbox for testing", False),
    ("SQUARE_ACCESS_TOKEN", "Access token (Square Developer Dashboard → your app → Credentials)", True),
    ("SQUARE_LOCATION_ID", "Location ID (Square Dashboard → Locations)", False),
    ("SQUARE_WEBHOOK_SIGNATURE_KEY", "Webhook signature key (optional — press Enter to skip)", True),
    ("SQUARE_WEBHOOK_NOTIFICATION_URL", "Public webhook URL, e.g. https://pinksheet.example.com/webhooks/square/ (optional)", False),
]


def set_env_values(env_path: Path, values: dict[str, str]) -> None:
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    remaining = dict(values)
    for index, line in enumerate(lines):
        match = re.match(r"^\s*#?\s*([A-Z0-9_]+)\s*=", line)
        if match and match.group(1) in remaining:
            key = match.group(1)
            lines[index] = f"{key}={remaining.pop(key)}"
    if remaining:
        lines.append("")
        lines.extend(f"{key}={value}" for key, value in remaining.items())
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class Command(BaseCommand):
    help = "Set up Square: asks for the keys and saves them in .env."

    def handle(self, *args, **options):
        env_path = Path(settings.BASE_DIR) / ".env"
        self.stdout.write("Square setup — press Enter to keep the current value.\n")
        values = {}
        for key, prompt, secret in FIELDS:
            current = settings.SQUARE.get(key.replace("SQUARE_", ""), "")
            shown = "(set)" if secret and current else current
            question = f"{prompt}\n  [{shown or 'empty'}]: "
            answer = (getpass.getpass(question) if secret else input(question)).strip()
            if answer:
                values[key] = answer
        if "SQUARE_ENVIRONMENT" in values and values["SQUARE_ENVIRONMENT"].lower() not in ("production", "sandbox"):
            raise CommandError("Environment must be 'production' or 'sandbox'.")
        if not values:
            self.stdout.write("Nothing changed.")
            return
        set_env_values(env_path, values)
        self.stdout.write(self.style.SUCCESS(f"Saved to {env_path}. Restart Dispodex (close and reopen start.bat)."))
        self.stdout.write("Then open System in the sidebar and press “Test connection”.")
