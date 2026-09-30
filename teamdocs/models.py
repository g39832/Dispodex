"""Team docs: posts anyone can write (a title, the details, photos underneath)."""
from django.conf import settings
from django.db import models
from django.utils import timezone


class DocPost(models.Model):
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    # Who wrote it, as shown on the post, plus the signed-in account when there is one.
    author = models.CharField(max_length=80)
    author_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    updated_by = models.CharField("last edited by", max_length=80, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "team doc"

    def __str__(self) -> str:
        return self.title


class DocPhoto(models.Model):
    """A photo shown under a post. The file lives in data/media/doc_photos/<post id>/."""

    post = models.ForeignKey(DocPost, on_delete=models.CASCADE, related_name="photos")
    original_name = models.CharField(max_length=255)
    stored_name = models.CharField(max_length=128)
    mime_type = models.CharField(max_length=64)
    file_size = models.PositiveIntegerField(default=0)
    sort_order = models.IntegerField(default=0)
    added_by = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return self.original_name
