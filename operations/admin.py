from django.contrib import admin

from operations.models import BugReport


@admin.register(BugReport)
class BugReportAdmin(admin.ModelAdmin):
    """Reports from the "Report a bug" button; a record of what was said, so read-only."""

    list_display = ("summary", "reporter", "sent_by", "page", "created_at")
    date_hierarchy = "created_at"
    search_fields = ("summary", "details", "reporter", "sent_by", "page")
    readonly_fields = ("created_at", "reporter", "sent_by", "summary", "details", "page", "browser")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
