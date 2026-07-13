from django.contrib import admin

from .models import Invite, InviteStatus, Membership, OpeningBalance, Org, ValuationConfig


class ValuationConfigInline(admin.StackedInline):
    model = ValuationConfig
    can_delete = False
    extra = 0


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0
    autocomplete_fields = ("user",)


@admin.register(Org)
class OrgAdmin(admin.ModelAdmin):
    list_display = ("slug", "display_name", "unit_name", "default_hourly_rate", "created_at")
    search_fields = ("slug", "display_name")
    prepopulated_fields = {"slug": ("display_name",)}
    inlines = [ValuationConfigInline, MembershipInline]


@admin.register(ValuationConfig)
class ValuationConfigAdmin(admin.ModelAdmin):
    list_display = ("org", "valuation_mode", "weight_window", "budget_enforcement")
    list_filter = ("valuation_mode", "weight_window", "budget_enforcement")


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "org", "role", "hourly_rate", "taiga_username")
    list_filter = ("role", "org")
    search_fields = ("user__email", "taiga_username")
    autocomplete_fields = ("user",)


@admin.register(Invite)
class InviteAdmin(admin.ModelAdmin):
    """Invite oversight + the revocation seam (mark selected invites revoked)."""

    list_display = ("name", "email", "org", "audience", "role", "status", "expires_at")
    list_filter = ("status", "audience", "org")
    search_fields = ("name", "email", "code")
    readonly_fields = ("code", "created_at")
    actions = ["revoke"]

    @admin.action(description="Revoke selected invites")
    def revoke(self, request, queryset):
        queryset.update(status=InviteStatus.REVOKED)


@admin.register(OpeningBalance)
class OpeningBalanceAdmin(admin.ModelAdmin):
    list_display = ("membership", "org", "value", "created_at")
    list_filter = ("org",)
    search_fields = ("membership__user__email", "source_note")
