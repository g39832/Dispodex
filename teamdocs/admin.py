from django.contrib import admin

from teamdocs.models import DocPost


@admin.register(DocPost)
class DocPostAdmin(admin.ModelAdmin):
    """Posts are written on the Team docs page; the admin lists them for reference."""

    list_display = ("title", "category", "author", "created_at", "pinned_at", "updated_by", "updated_at")
    list_filter = ("category",)
    date_hierarchy = "created_at"
    search_fields = ("title", "body", "author", "category")
    readonly_fields = (
        "title", "category", "body", "author", "author_user", "created_at", "updated_by", "updated_at",
        "pinned_at", "pinned_by",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
