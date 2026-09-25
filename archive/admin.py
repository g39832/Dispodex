from django.contrib import admin

from archive.models import ArchiveItem


@admin.register(ArchiveItem)
class ArchiveItemAdmin(admin.ModelAdmin):
    list_display = ("sku", "title", "status", "sold_at", "sold_price", "legacy_source")
    list_filter = ("status", "legacy_source")
    search_fields = ("sku", "title", "buyer", "notes", "legacy_id")
