from django.contrib import admin

from .models import Edition, Send


class SendInline(admin.TabularInline):
    model = Send
    extra = 0


@admin.register(Edition)
class EditionAdmin(admin.ModelAdmin):
    list_display = ("org_slug", "week_number", "window_start", "window_end", "updated_at")
    list_filter = ("org_slug",)
    inlines = [SendInline]
