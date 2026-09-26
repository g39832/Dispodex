"""Fill a demo copy of Dispodex with made-up inventory. Refuses to run unless demo mode is on."""
import io
import random
import shutil
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageDraw

from inventory.models import (
    Condition,
    Functional,
    IntakeDraft,
    Item,
    ItemEvent,
    ListingImageLayout,
    Photo,
    Review,
    ScriptCache,
    Status,
)
from inventory.services import photos as photo_service
from squaresync.models import SyncJob

STAFF = ["Alex", "Jordan", "Sam", "Riley", "Casey"]
SOURCES = ["School district auction", "Office cleanout", "Walk-in trade", "Recycling pickup", "Estate sale"]
LOCATIONS = ["Shelf A2", "Shelf B1", "Bench 3", "eBay room", "Front counter"]

# (what it is, brand & model, drawing, price range, cpu, ram, storage)
CATALOG = [
    ("Laptop", "Dell Latitude 5510", "laptop", (120, 220), "Intel Core i5-10310U", "16GB", "256"),
    ("Laptop", "Lenovo ThinkPad T480", "laptop", (150, 260), "Intel Core i5-8350U", "8GB", "256"),
    ("Laptop", "HP EliteBook 840 G6", "laptop", (160, 240), "Intel Core i7-8665U", "16GB", "512"),
    ("Chromebook", "HP Chromebook 14", "laptop", (45, 80), "Intel Celeron N4000", "4GB", "32"),
    ("Laptop", "Dell Latitude 7490", "laptop", (130, 210), "Intel Core i5-8250U", "8GB", "256"),
    ("Desktop", "Lenovo ThinkCentre M920q", "tower", (150, 230), "Intel Core i5-8500T", "16GB", "256"),
    ("Desktop", "Dell OptiPlex 7070", "tower", (140, 220), "Intel Core i7-9700", "16GB", "512"),
    ("Desktop", "HP ProDesk 600 G4", "tower", (110, 180), "Intel Core i5-8500", "8GB", "256"),
    ("Monitor", "Dell P2419H 24\"", "monitor", (60, 95), "", "", ""),
    ("Monitor", "HP EliteDisplay E243 24\"", "monitor", (55, 90), "", "", ""),
    ("Tablet", "Apple iPad 7th Gen", "tablet", (110, 170), "", "", "32"),
    ("Power supply", "EVGA 500W", "psu", (20, 40), "", "", ""),
    ("Power supply", "Corsair CX650M", "psu", (35, 60), "", "", ""),
    ("RAM", "Crucial 16GB DDR4 SODIMM", "ram", (18, 35), "", "16GB", ""),
    ("RAM", "Kingston 8GB DDR4 DIMM", "ram", (10, 20), "", "8GB", ""),
    ("Network switch", "Cisco Catalyst 2960 24-port", "switch", (40, 90), "", "", ""),
]

LANE_COUNTS = [
    (Status.INTAKE, 9),
    (Status.EBAY_DRAFT, 7),
    (Status.EBAY_REVIEW, 5),
    (Status.EBAY_LISTED, 11),
    (Status.STORE, 8),
    (Status.SOLD, 8),
]

PALETTES = [
    ((226, 236, 250), (70, 92, 130)),
    ((232, 228, 248), (88, 76, 140)),
    ((225, 242, 238), (52, 110, 96)),
    ((246, 236, 226), (130, 92, 60)),
]


def _draw_item(kind: str, rng: random.Random) -> io.BytesIO:
    """A simple flat product illustration, so demo cards have pictures without real photos."""
    background, ink = rng.choice(PALETTES)
    image = Image.new("RGB", (800, 600), background)
    draw = ImageDraw.Draw(image)
    light = tuple(min(255, channel + 150) for channel in ink)

    if kind == "laptop":
        draw.rounded_rectangle((220, 150, 580, 390), 16, fill=ink)
        draw.rounded_rectangle((238, 168, 562, 372), 6, fill=light)
        draw.polygon([(160, 400), (640, 400), (600, 440), (200, 440)], fill=ink)
    elif kind == "tower":
        draw.rounded_rectangle((300, 110, 500, 490), 18, fill=ink)
        draw.ellipse((385, 150, 415, 180), fill=light)
        for y in range(230, 440, 22):
            draw.line((330, y, 470, y), fill=light, width=5)
    elif kind == "monitor":
        draw.rounded_rectangle((170, 120, 630, 400), 14, fill=ink)
        draw.rectangle((188, 138, 612, 382), fill=light)
        draw.rectangle((380, 400, 420, 460), fill=ink)
        draw.rounded_rectangle((300, 460, 500, 482), 8, fill=ink)
    elif kind == "tablet":
        draw.rounded_rectangle((280, 100, 520, 500), 26, fill=ink)
        draw.rounded_rectangle((298, 130, 502, 462), 8, fill=light)
    elif kind == "psu":
        draw.rounded_rectangle((210, 170, 590, 430), 14, fill=ink)
        draw.ellipse((300, 210, 480, 390), outline=light, width=10)
        for angle in range(0, 360, 45):
            box = (330, 240, 450, 360)
            draw.pieslice(box, angle, angle + 20, fill=light)
    elif kind == "ram":
        draw.rectangle((150, 240, 650, 360), fill=ink)
        for x in range(190, 620, 90):
            draw.rectangle((x, 262, x + 60, 322), fill=light)
        for x in range(160, 640, 14):
            draw.rectangle((x, 360, x + 7, 380), fill=(200, 170, 80))
    else:  # switch
        draw.rounded_rectangle((120, 250, 680, 350), 10, fill=ink)
        for x in range(160, 640, 40):
            draw.rectangle((x, 280, x + 26, 304), fill=light)
            draw.rectangle((x, 310, x + 26, 330), fill=light)

    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    buffer.seek(0)
    return buffer


class Command(BaseCommand):
    help = "Replace everything in a DEMO copy of Dispodex with made-up inventory (PINKSHEET_DEMO_MODE=1 only)."

    def add_arguments(self, parser):
        parser.add_argument("--seed", type=int, default=2026, help="Random seed, so every reset looks the same.")

    def handle(self, *args, **options):
        if not settings.PINKSHEET["DEMO_MODE"]:
            raise CommandError(
                "seed_demo deletes every item. It only runs when PINKSHEET_DEMO_MODE=1 is set in .env, "
                "which must never be the case on the real shop server."
            )
        rng = random.Random(options["seed"])
        self._wipe()
        count = self._seed(rng)
        self.stdout.write(self.style.SUCCESS(f"Demo ready: {count} made-up items."))

    def _wipe(self):
        with transaction.atomic():
            ItemEvent.objects.all().delete()
            Item.all_objects.all().delete()
            Photo.objects.all().delete()
            IntakeDraft.objects.all().delete()
            ScriptCache.objects.all().delete()
            ListingImageLayout.objects.all().delete()
            SyncJob.objects.all().delete()
        for folder in ("sku_photos", "thumbs", "ebay_images"):
            shutil.rmtree(Path(settings.MEDIA_ROOT) / folder, ignore_errors=True)

    def _seed(self, rng: random.Random) -> int:
        now = timezone.now()
        number = 1000
        created = 0
        for status, count in LANE_COUNTS:
            for _ in range(count):
                number += rng.randint(1, 7)
                what, model, kind, (low, high), cpu, ram, storage = rng.choice(CATALOG)
                sku = f"DX-{number}"
                received = now - timedelta(days=rng.randint(1, 40), hours=rng.randint(0, 20))

                price = Decimal(rng.randrange(low, high)) + (Decimal("0.99") if rng.random() < 0.4 else 0)
                os_name = "ChromeOS" if what == "Chromebook" else "Windows 11 Pro" if kind in ("laptop", "tower") else ""

                # Photos first: before the item exists they add no history noise
                for _photo in range(rng.randint(1, 2)):
                    photo_service.save_sku_photo(sku, _draw_item(kind, rng), f"{kind}.png")

                item = Item(
                    sku=sku,
                    what_is_it=what,
                    brand_model=model,
                    status=status,
                    date_received=received.date(),
                    source=rng.choice(SOURCES),
                    where_it_goes=rng.choice(LOCATIONS),
                    functional=Functional.YES,
                    condition=rng.choice([Condition.GOOD, Condition.GREAT, Condition.GREAT, Condition.EXCELLENT]),
                    cpu=cpu,
                    ram=ram,
                    ssd_gb=storage,
                    os=os_name,
                    price=price,
                    reviewed=Review.ACTIVE if status == Status.EBAY_LISTED else Review.INACTIVE,
                    ready=status in (Status.EBAY_REVIEW, Status.EBAY_LISTED, Status.STORE),
                    created_at=received,
                )
                item.apply_status(status)
                item.updated_at = received + timedelta(hours=rng.randint(1, 72))
                item.save(touch=False)

                ItemEvent.objects.create(
                    item=item, sku_normalized=item.sku_normalized, action=ItemEvent.Action.CREATED,
                    actor=rng.choice(STAFF), created_at=received,
                )
                if status != Status.INTAKE:
                    ItemEvent.objects.create(
                        item=item, sku_normalized=item.sku_normalized, action=ItemEvent.Action.EDITED,
                        changes=[{"field": "status", "label": "Status", "old": "Intake", "new": item.status_label}],
                        actor=rng.choice(STAFF), created_at=item.updated_at,
                    )
                created += 1
        return created
