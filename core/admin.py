"""Let staff (not only superusers) manage the team's accounts from "Manage users".

Staff can add people, reset passwords and turn accounts on or off. They can't hand
out superuser rights or Django permissions, and superuser accounts are read-only to
them, so nobody can lock the owner out or promote themselves.
"""
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin

User = get_user_model()

# Only a superuser may set these.
SUPERUSER_ONLY_FIELDS = ("is_superuser", "groups", "user_permissions")


def is_team_manager(user) -> bool:
    return user.is_active and user.is_staff


class TeamUserAdmin(UserAdmin):
    def has_module_permission(self, request):
        return is_team_manager(request.user)

    def has_view_permission(self, request, obj=None):
        return is_team_manager(request.user)

    def has_add_permission(self, request):
        return is_team_manager(request.user)

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return is_team_manager(request.user) and not (obj and obj.is_superuser)

    def has_delete_permission(self, request, obj=None):
        return self.has_change_permission(request, obj)

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        if request.user.is_superuser:
            return fields
        return (*fields, *SUPERUSER_ONLY_FIELDS)


admin.site.unregister(User)
admin.site.register(User, TeamUserAdmin)
