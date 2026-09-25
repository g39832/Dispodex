"""Read-only history imported from the old systems (sold items, legacy inventory)."""
from django.db import models
from django.db.models import Q
from django.utils import timezone


class ArchiveItem(models.Model):
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(null=True, blank=True)
    sku = models.CharField(max_length=128, blank=True)
    sku_normalized = models.CharField(max_length=128, blank=True, db_index=True)
    title = models.CharField(max_length=1024, blank=True)
    status = models.CharField(max_length=255, blank=True)
    sold_at = models.DateField(null=True, blank=True)
    sold_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    purchase_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    source = models.CharField(max_length=255, blank=True)
    buyer = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    legacy_source = models.CharField(max_length=255, blank=True)
    legacy_table = models.CharField(max_length=255, blank=True)
    legacy_id = models.CharField(max_length=128, blank=True)
    legacy_location_id = models.CharField(max_length=128, blank=True)
    legacy_category_id = models.CharField(max_length=128, blank=True)
    legacy_payload = models.TextField(blank=True, help_text="The original row, kept for auditing.")

    class Meta:
        ordering = ["-sold_at", "-updated_at", "-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "sold_at"]),
            models.Index(fields=["legacy_source", "legacy_table"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["legacy_source", "legacy_table", "legacy_id"],
                condition=~Q(legacy_id=""),
                name="unique_legacy_identity",
            )
        ]

    def __str__(self) -> str:
        return f"{self.sku} — {self.title}"

    @property
    def display_title(self) -> str:
        return self.title.strip() or self.notes.strip()

    @property
    def legacy_label(self) -> str:
        parts = [p for p in (self.legacy_source, self.legacy_table, self.legacy_id) if p.strip()]
        return " / ".join(parts)

    @property
    def payload_pretty(self) -> str:
        import json

        try:
            return json.dumps(json.loads(self.legacy_payload), indent=2, ensure_ascii=False)
        except (ValueError, TypeError):
            return self.legacy_payload or "{}"
