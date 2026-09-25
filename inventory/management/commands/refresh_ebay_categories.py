import requests
from django.core.management.base import BaseCommand, CommandError

from inventory.services import ebay_categories


class Command(BaseCommand):
    help = "Download eBay's live electronics categories for the intake picker (needs EBAY_CLIENT_ID/SECRET)."

    def handle(self, *args, **options):
        try:
            count = ebay_categories.refresh_from_ebay()
        except (RuntimeError, requests.RequestException) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Saved {count} categories to {ebay_categories.snapshot_path()}."))
