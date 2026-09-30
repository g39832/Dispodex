"""Small key/value store for background-job bookkeeping (last backup run, worker heartbeat…)."""
from django.db import models
from django.utils import timezone


class SystemState(models.Model):
    key = models.CharField(max_length=64, unique=True)
    value = models.TextField(blank=True)
    updated_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return f"{self.key}={self.value}"

    @classmethod
    def get(cls, key: str, default: str = "") -> str:
        row = cls.objects.filter(key=key).first()
        return row.value if row else default

    @classmethod
    def set(cls, key: str, value: str) -> None:
        cls.objects.update_or_create(key=key, defaults={"value": value, "updated_at": timezone.now()})


class BugReport(models.Model):
    """A problem someone reported from the "Report a bug" button; listed in the admin."""

    created_at = models.DateTimeField(default=timezone.now)
    reporter = models.CharField("name", max_length=80)
    # The signed-in account or device that sent it, whatever name was typed.
    sent_by = models.CharField("sent from account or device", max_length=80, blank=True)
    summary = models.CharField("what went wrong", max_length=200)
    details = models.TextField("what they were doing", blank=True)
    page = models.CharField(max_length=500, blank=True)
    browser = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.summary
