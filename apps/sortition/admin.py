from django.contrib import admin

from .models import SortitionDraw


@admin.register(SortitionDraw)
class SortitionDrawAdmin(admin.ModelAdmin):
    list_display = ("id", "org", "seats", "weight_window", "seed", "created_at")
    list_filter = ("org", "weight_window")
