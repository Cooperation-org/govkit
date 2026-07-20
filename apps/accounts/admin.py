from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import ProfileLink, User


class ProfileLinkInline(admin.TabularInline):
    model = ProfileLink
    extra = 0
    fields = ("kind", "label", "handle", "url", "order", "is_public")


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    inlines = [ProfileLinkInline]
    ordering = ("email",)
    list_display = ("email", "display_name", "auth_provider", "is_staff", "is_active")
    list_filter = ("is_staff", "is_superuser", "is_active", "auth_provider")
    search_fields = ("email", "display_name", "auth_provider_id")
    readonly_fields = ("last_login", "date_joined")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("display_name", "avatar_url", "bio")}),
        ("External identity", {"fields": ("auth_provider", "auth_provider_id")}),
        (
            "Permissions",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),)
