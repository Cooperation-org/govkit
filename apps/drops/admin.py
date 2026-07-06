from django.contrib import admin

from .models import DropLine, DropRun


class DropLineInline(admin.TabularInline):
    model = DropLine
    extra = 0
    autocomplete_fields = ("membership",)
    fields = ("membership", "computed_value", "adjustment", "adjustment_reason", "final_value")


@admin.register(DropRun)
class DropRunAdmin(admin.ModelAdmin):
    list_display = ("id", "org", "state", "opened_by", "opened_at", "approved_at")
    list_filter = ("org", "state")
    inlines = [DropLineInline]


@admin.register(DropLine)
class DropLineAdmin(admin.ModelAdmin):
    list_display = ("id", "run", "membership", "computed_value", "adjustment", "final_value")
    list_filter = ("org", "run__state")
    search_fields = ("membership__user__email", "adjustment_reason")
    autocomplete_fields = ("membership",)
    filter_horizontal = ("tasks",)
