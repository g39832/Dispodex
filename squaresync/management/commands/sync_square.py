from django.core.management.base import BaseCommand, CommandError

from inventory.models import Item
from squaresync import sync as square_sync
from squaresync.config import get_config


class Command(BaseCommand):
    help = "Push one SKU (or every SKU with --all) to Square right now."

    def add_arguments(self, parser):
        parser.add_argument("sku", nargs="?")
        parser.add_argument("--all", action="store_true")

    def handle(self, *args, **options):
        if not get_config().enabled:
            raise CommandError("Square isn't configured. Fill in SQUARE_ACCESS_TOKEN and SQUARE_LOCATION_ID in .env.")
        if options["all"]:
            skus = list(Item.objects.exclude(sku_normalized="").values_list("sku_normalized", flat=True))
        elif options["sku"]:
            skus = [options["sku"]]
        else:
            raise CommandError("Give a SKU, or use --all.")
        failures = 0
        for sku in skus:
            result = square_sync.sync_item(sku)
            failures += result.status == "error"
            self.stdout.write(f"[{result.status.upper()}] {sku}: {result.message}")
        if failures:
            raise CommandError(f"{failures} SKU(s) failed.")
