from django.contrib import admin

from .forms import ToolConnectionForm
from .models import AccessChange, ToolConnection


@admin.register(ToolConnection)
class ToolConnectionAdmin(admin.ModelAdmin):
    form = ToolConnectionForm
    list_display = ("display_name", "org", "kind", "base_url", "admin_login", "enabled")
    list_filter = ("kind", "enabled", "org")
    readonly_fields = ("admin_secret_status",)

    def get_fields(self, request, obj=None):
        return [*ToolConnectionForm.Meta.fields, "org", "admin_secret_input", "admin_secret_status"]

    @admin.display(description="Admin password")
    def admin_secret_status(self, obj):
        return "set (hidden)" if obj and obj.admin_secret else "unset"


@admin.register(AccessChange)
class AccessChangeAdmin(admin.ModelAdmin):
    list_display = ("done_at", "org", "connection", "scope", "email", "action", "result", "done_by")
    list_filter = ("org", "action", "result")
    search_fields = ("email", "scope", "detail")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
