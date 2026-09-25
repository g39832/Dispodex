from django.contrib import admin

from inventory.models import IntakeDraft, Item, ItemEvent, ListingImageLayout, Photo, ScriptCache


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ("sku", "what_is_it", "status", "price", "quantity", "updated_at", "deleted_at")
    list_filter = ("status", "condition", "deleted_at")
    search_fields = ("sku", "sku_normalized", "what_is_it", "brand_model", "serial_number", "notes")
    readonly_fields = ("sku_normalized", "created_at", "updated_at")

    def get_queryset(self, request):
        # Show deleted items too, so they can be inspected or restored here.
        return Item.all_objects.all()


@admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    list_display = ("id", "sku_normalized", "original_name", "is_thumb", "sort_order", "file_size", "created_at")
    search_fields = ("sku_normalized", "original_name")


@admin.register(IntakeDraft)
class IntakeDraftAdmin(admin.ModelAdmin):
    list_display = ("sku_normalized", "version", "updated_at")
    search_fields = ("sku_normalized",)


@admin.register(ScriptCache)
class ScriptCacheAdmin(admin.ModelAdmin):
    list_display = ("sku_display", "state", "updated_at")
    search_fields = ("sku_normalized",)


admin.site.register(ListingImageLayout)
admin.site.site_header = "Dispodex admin"
admin.site.site_title = "Dispodex admin"


@admin.register(ItemEvent)
class ItemEventAdmin(admin.ModelAdmin):
    """History is a record of what happened, so it is read-only here."""

    list_display = ("created_at", "sku_normalized", "action", "actor", "note")
    list_filter = ("action",)
    search_fields = ("sku_normalized", "actor", "note")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
