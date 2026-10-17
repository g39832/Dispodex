"""Scrap: things weighed on the shop scale, then carried over to the scrap side.

An entry is weighed first ("Waiting to move"), then someone marks it moved to
scrap. Both steps keep who did them and when, so the scrap team can say how
many pounds went over and who handled it. Entries have no SKU.
"""
from django.db import models
from django.utils import timezone


class ScrapCategory(models.Model):
    """A kind of scrap the team made up ("Laptops", "Cables" …). Hidden ones stay on old entries."""

    name = models.CharField(max_length=40, unique=True)
    hidden = models.BooleanField(default=False)
    created_by = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "scrap categories"

    def __str__(self) -> str:
        return self.name


class ScrapEntry(models.Model):
    class Source(models.TextChoices):
        TYPED = "typed", "Typed in"
        SCALE = "scale", "Scale"
        IMPORT = "import", "Imported"

    class DropOff(models.TextChoices):
        PUBLIC = "public", "Public drop-off"
        COMPANY = "company", "Company drop-off"

    # Where it came from. Blank only on entries from before this was asked, or imports that didn't say.
    drop_off = models.CharField(max_length=10, choices=DropOff.choices, blank=True, db_index=True)
    company = models.CharField("company name", max_length=120, blank=True)
    description = models.CharField("what it is", max_length=200, blank=True)
    weight_lbs = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.ForeignKey(ScrapCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name="entries")
    note = models.CharField(max_length=255, blank=True)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.TYPED)
    # Who took it in and when. Usually the person entering it, right now, but either can be set
    # when logging for someone else or after the fact; added_by always keeps who typed it in.
    weighed_by = models.CharField("taken in by", max_length=80)
    weighed_at = models.DateTimeField("taken in", default=timezone.now, db_index=True)
    added_by = models.CharField(max_length=80, blank=True)
    # Blank until someone carries it to the scrap side.
    moved_by = models.CharField(max_length=80, blank=True)
    moved_at = models.DateTimeField(null=True, blank=True, db_index=True)
    edited_by = models.CharField("last edited by", max_length=80, blank=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    # Imported rows remember a fingerprint of their CSV row, so importing the same sheet again skips them.
    import_key = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        ordering = ["-weighed_at", "-id"]
        verbose_name_plural = "scrap entries"

    def __str__(self) -> str:
        return f"{self.weight_lbs} lbs {self.description or 'scrap'}"

    @property
    def is_moved(self) -> bool:
        return self.moved_at is not None

