"""Computers reported by the imaging app, one row per serial number."""
from django.db import models
from django.utils import timezone


class DeviceReport(models.Model):
    serial = models.CharField(max_length=128, unique=True)
    manufacturer = models.CharField(max_length=128, blank=True)
    model = models.CharField(max_length=128, blank=True)
    # The imaging app's own progress, as it last reported it.
    status = models.CharField(max_length=64, blank=True)
    stage = models.CharField(max_length=128, blank=True)
    progress = models.PositiveSmallIntegerField(null=True, blank=True)
    message = models.CharField(max_length=500, blank=True)
    # The last JSON received, exactly as sent, and a short log of status changes.
    payload = models.JSONField(default=dict)
    log = models.JSONField(default=list)
    item = models.ForeignKey("inventory.Item", null=True, blank=True, on_delete=models.SET_NULL, related_name="device_reports")
    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now, db_index=True)
    reported_by = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["-last_seen", "-id"]

    def __str__(self) -> str:
        return self.serial

    @property
    def title(self) -> str:
        from imaging.ingest import brand_model

        return brand_model(self.manufacturer, self.model) or "Unknown computer"
