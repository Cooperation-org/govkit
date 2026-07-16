from django.contrib import admin

from .models import Deal, Payout, Project, ProjectLink, Split


class ProjectLinkInline(admin.TabularInline):
    model = ProjectLink
    extra = 0


class SplitInline(admin.TabularInline):
    model = Split
    extra = 0


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ["name", "org", "kind", "status", "due"]
    list_filter = ["kind", "status", "org"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ["name"]}
    inlines = [ProjectLinkInline]


@admin.register(Deal)
class DealAdmin(admin.ModelAdmin):
    list_display = ["project", "budget_total", "currency", "agreed_on"]
    inlines = [SplitInline]


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = ["project", "membership", "amount", "paid_on"]
    list_filter = ["project__org"]
