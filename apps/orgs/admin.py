from django.contrib import admin

from .models import (
    ExternalHolder,
    Invite,
    InviteStatus,
    Membership,
    OpeningBalance,
    Org,
    OrgStake,
    ValuationConfig,
)


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


@admin.register(ExternalHolder)
class ExternalHolderAdmin(admin.ModelAdmin):
    """Outside companies that can hold equity in a venture without being an org here."""

    list_display = ("display_name", "slug", "url", "created_at")
    search_fields = ("display_name", "slug")
    prepopulated_fields = {"slug": ("display_name",)}


@admin.register(OrgStake)
class OrgStakeAdmin(admin.ModelAdmin):
    """A sponsor's share of a venture, including one added after the venture exists.

    This is the seam for a venture that was created before its terms were recorded:
    add the sponsor's stake here and give the founder the matching opening balance
    above, so the pie ends up where the invite said it would.
    """

    list_display = ("holder", "org", "value", "created_at")
    list_filter = ("holder", "org")
    search_fields = ("holder__display_name", "org__slug", "source_note")
    autocomplete_fields = ("holder", "granted_by")
