from django.contrib import admin

from .models import Idea, IdeaInterest


class IdeaInterestInline(admin.TabularInline):
    model = IdeaInterest
    extra = 0
    fields = ("user", "kind", "created_at")
    readonly_fields = ("created_at",)


@admin.register(Idea)
class IdeaAdmin(admin.ModelAdmin):
    list_display = ("title", "created_by", "created_at", "is_active")
    list_filter = ("is_active",)
    search_fields = ("title", "pitch", "created_by__email")
    prepopulated_fields = {"slug": ("title",)}
    inlines = [IdeaInterestInline]
