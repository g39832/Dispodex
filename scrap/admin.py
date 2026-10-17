from django.contrib import admin

from scrap.models import ScrapCategory, ScrapEntry


@admin.register(ScrapEntry)
class ScrapEntryAdmin(admin.ModelAdmin):
    """Entries are added on the Scrap page; the admin lists them for reference."""

    list_display = ("weighed_at", "weighed_by", "weight_lbs", "description", "category", "moved_at", "moved_by", "source")
    list_filter = ("category", "source")
    date_hierarchy = "weighed_at"
    search_fields = ("description", "note", "weighed_by", "moved_by", "added_by")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ScrapCategory)
class ScrapCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "hidden", "created_by", "created_at")
