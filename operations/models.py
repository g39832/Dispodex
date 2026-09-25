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
