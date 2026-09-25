"""Inventory data: intake items, their photos, autosave drafts and eBay scripts."""
from decimal import Decimal

from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.skus import normalize_sku, sku_directory


class Status(models.TextChoices):
    """The status-board lanes, in board order. Stored values match the PHP app."""

    INTAKE = "intake", "Intake"
    EBAY_DRAFT = "ebay draft", "eBay Draft"
    EBAY_REVIEW = "ebay review", "eBay Review"
    EBAY_LISTED = "ebay listed", "eBay Listed"
    STORE = "dispo tech store", "Dispo Tech Store"
    SOLD = "sold", "SOLD"


# Old status spellings found in the PHP database, mapped to today's lanes.
LEGACY_STATUS_MAP = {
    "sold": Status.SOLD,
    "tested": Status.EBAY_DRAFT,
    "description": Status.EBAY_DRAFT,
    "ready for ebay listing": Status.EBAY_REVIEW,
    "listed": Status.EBAY_LISTED,
    "ebay listed": Status.EBAY_LISTED,
    "dispo tech store": Status.STORE,
    "intake": Status.INTAKE,
    "ready": Status.EBAY_REVIEW,
}


def coerce_status(value: object) -> str:
    """Turn any stored/submitted status into one of the six lane values."""
    key = str(value or "").strip().lower()
    if key in Status.values:
        return key
    return LEGACY_STATUS_MAP.get(key, Status.INTAKE)


class YesNo(models.TextChoices):
    YES = "Yes", "Yes"
    NO = "No", "No"


class Functional(models.TextChoices):
    YES = "Yes", "Yes"
    NO = "No", "No"
    UNKNOWN = "Unknown", "Unknown"


class Condition(models.TextChoices):
    """Worst to best. "Unicorn" from the old scale is stored as Excellent."""

    SCRAP = "Scrap", "Scrap"
    POOR = "Poor", "Poor"
    FAIR = "Fair", "Fair"
    GOOD = "Good", "Good"
    GREAT = "Great", "Great"
    EXCELLENT = "Excellent", "Excellent"


# Condition values from older versions, mapped onto today's scale.
LEGACY_CONDITION_MAP = {"unicorn": Condition.EXCELLENT}


class CompatibleOS(models.TextChoices):
    WIN10 = "Windows 10", "Windows 10"
    WIN11 = "Windows 11", "Windows 11"
    LINUX = "Linux", "Linux"


class Review(models.IntegerChoices):
    """The ACTIVE / INACTIVE / SOLD badge on status-board cards."""

    INACTIVE = 0, "INACTIVE"
    ACTIVE = 1, "ACTIVE"
    SOLD = 2, "SOLD"


class ActiveItemManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class Item(models.Model):
    """One physical inventory item (one row of the old ``intake_items`` table)."""

    sku = models.CharField(max_length=64)
    sku_normalized = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.INTAKE, db_index=True)
    what_is_it = models.CharField("What is it?", max_length=255)

    ebay_category = models.CharField(max_length=255, blank=True)
    ebay_category_path = models.CharField(max_length=512, blank=True)
    ebay_category_id = models.CharField(max_length=32, blank=True)

    date_received = models.DateField(null=True, blank=True)
    source = models.CharField("Where did it come from?", max_length=255, blank=True)
    where_it_goes = models.CharField("Location / where it goes", max_length=255, blank=True)

    # (D1) Intake tasks
    functional = models.CharField(max_length=16, choices=Functional.choices, blank=True)
    condition = models.CharField(max_length=16, choices=Condition.choices, blank=True)
    # No longer on the intake sheet (removed 2026-09). Kept so answers on older items aren't lost.
    is_square = models.BooleanField("Is it a Square item?", default=False)
    care_if_square = models.BooleanField("Do we care about this item?", default=False)
    cords_adapters = models.CharField("Cords / adapters included?", max_length=8, choices=YesNo.choices, blank=True)
    keep_items_together = models.CharField("Keep items together?", max_length=8, choices=YesNo.choices, blank=True)
    picture_taken = models.CharField("Picture", max_length=8, choices=YesNo.choices, blank=True)

    # (D2) Description tasks
    power_on = models.CharField("Does it power on and stay on?", max_length=8, choices=YesNo.choices, blank=True)
    brand_model = models.CharField("Brand & model number", max_length=255, blank=True)
    ram = models.CharField("RAM", max_length=255, blank=True)
    ssd_gb = models.CharField("SSD GB", max_length=255, blank=True)
    cpu = models.CharField("CPU", max_length=255, blank=True)
    os = models.CharField("OS", max_length=255, blank=True)
    compatible_os = models.CharField(max_length=32, choices=CompatibleOS.choices, blank=True)
    battery_health = models.CharField(max_length=255, blank=True)
    graphics_card = models.CharField(max_length=255, blank=True)
    screen_resolution = models.CharField(max_length=255, blank=True)
    diagnostics_test_ran = models.BooleanField(default=False)
    wifi_card_installed = models.BooleanField(default=False)
    serial_number = models.CharField(max_length=128, blank=True)

    # Kept from the old sheet so no historical data is lost.
    ebay_status = models.CharField("eBay status", max_length=255, blank=True)
    in_ebay_room = models.CharField("In eBay room", max_length=255, blank=True)
    what_box = models.CharField("What box", max_length=255, blank=True)

    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    notes = models.TextField(blank=True)

    reviewed = models.PositiveSmallIntegerField(choices=Review.choices, default=Review.INACTIVE)
    ready = models.BooleanField(default=False)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(default=timezone.now, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    objects = ActiveItemManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["-updated_at", "-id"]
        base_manager_name = "all_objects"
        constraints = [
            models.UniqueConstraint(
                fields=["sku_normalized"],
                condition=Q(deleted_at__isnull=True) & ~Q(sku_normalized=""),
                name="unique_active_sku",
            )
        ]
        indexes = [models.Index(fields=["status", "updated_at"])]

    def __str__(self) -> str:
        return f"{self.sku} — {self.what_is_it}"

    def save(self, *args, touch: bool = True, **kwargs):
        self.sku = (self.sku or "").strip().upper()
        self.sku_normalized = normalize_sku(self.sku)
        self.status = coerce_status(self.status)
        if self.quantity is None or self.quantity < 1:
            self.quantity = 1
        if touch:
            self.updated_at = timezone.now()
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"updated_at"}
        super().save(*args, **kwargs)

    # ── status helpers ────────────────────────────────────────────────
    def apply_status(self, status: str) -> None:
        """Set the lane and keep the SOLD badge in step with it."""
        new_status = coerce_status(status)
        if new_status == Status.SOLD:
            self.reviewed = Review.SOLD
        elif self.status == Status.SOLD and self.reviewed == Review.SOLD:
            self.reviewed = Review.INACTIVE
        self.status = new_status

    @property
    def is_sold(self) -> bool:
        return self.status == Status.SOLD

    @property
    def status_label(self) -> str:
        return Status(self.status).label if self.status in Status.values else self.status

    @property
    def price_display(self) -> str:
        return f"${self.price:,.2f}" if self.price is not None else ""

    @property
    def total_value(self) -> Decimal:
        return (self.price or Decimal("0")) * self.quantity


class Photo(models.Model):
    """A photo attached to a SKU. The image file lives on disk, not in the database.

    Photos are keyed by SKU (not by item) so they can be added the moment a SKU
    is typed, before the item has ever been saved.
    """

    sku_normalized = models.CharField(max_length=64, db_index=True)
    original_name = models.CharField(max_length=255)
    stored_name = models.CharField(max_length=128)
    mime_type = models.CharField(max_length=64)
    file_size = models.PositiveIntegerField(default=0)
    is_thumb = models.BooleanField(default=False)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    # Small preview copied from a spreadsheet export (not the original photo): shown with a
    # "Low-res" badge so it can be retaken or replaced by the original later.
    low_res = models.BooleanField(default=False)

    class Meta:
        ordering = ["sort_order", "id"]
        indexes = [models.Index(fields=["sku_normalized", "is_thumb", "id"])]

    def __str__(self) -> str:
        return f"{self.sku_normalized}: {self.original_name}"

    @property
    def relative_path(self) -> str:
        return f"sku_photos/{sku_directory(self.sku_normalized)}/{self.stored_name}"


class IntakeDraft(models.Model):
    """Unsaved intake-form changes, autosaved while someone types."""

    sku_normalized = models.CharField(max_length=64, unique=True)
    payload = models.JSONField(default=dict)
    version = models.PositiveIntegerField(default=1)
    client_id = models.CharField(max_length=64, blank=True)
    updated_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return f"Draft {self.sku_normalized} v{self.version}"


class ScriptCache(models.Model):
    """The eBay Script Builder's saved prompt, pasted ChatGPT text and final script."""

    sku_normalized = models.CharField(max_length=64, unique=True)
    sku_display = models.CharField(max_length=64)
    prompt_text = models.TextField(blank=True)
    chatgpt_text = models.TextField(blank=True)
    final_text = models.TextField(blank=True)
    updated_at = models.DateTimeField(default=timezone.now, db_index=True)

    def __str__(self) -> str:
        return f"Script {self.sku_display}"

    @property
    def state(self) -> str:
        """ready / draft / prompt / none — used for the coloured script dot."""
        if self.final_text.strip():
            return "ready"
        if self.chatgpt_text.strip():
            return "draft"
        if self.prompt_text.strip():
            return "prompt"
        return "none"


class ListingImageLayout(models.Model):
    """Positions of images on the eBay listing-image composer canvas."""

    sku_normalized = models.CharField(max_length=64, unique=True)
    positions = models.JSONField(default=list)
    updated_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return f"Layout {self.sku_normalized}"


class ItemEvent(models.Model):
    """One entry in an item's history: what changed, who changed it, and when."""

    class Action(models.TextChoices):
        CREATED = "created", "Created"
        EDITED = "edited", "Edited"
        DELETED = "deleted", "Deleted"
        RESTORED = "restored", "Restored"
        PHOTO_ADDED = "photo_added", "Photo added"
        PHOTO_REMOVED = "photo_removed", "Photo removed"

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="events")
    sku_normalized = models.CharField(max_length=64, db_index=True)
    action = models.CharField(max_length=20, choices=Action.choices)
    # [{"field": "price", "label": "Price", "old": "$200.00", "new": "$249.99"}, ...]
    changes = models.JSONField(default=list, blank=True)
    note = models.CharField(max_length=255, blank=True)
    actor = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["item", "created_at"])]

    def __str__(self) -> str:
        return f"{self.sku_normalized} {self.action} by {self.actor}"
