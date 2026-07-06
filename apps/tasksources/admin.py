from django.contrib import admin

from .models import TaskSourceConfig, TrackedTask


@admin.register(TaskSourceConfig)
class TaskSourceConfigAdmin(admin.ModelAdmin):
    list_display = ("org", "adapter_type", "base_url", "project_selector", "updated_at")
    list_filter = ("adapter_type", "org")
    # api_token is encrypted at rest; do not surface in list views.
    exclude = ()


@admin.register(TrackedTask)
class TrackedTaskAdmin(admin.ModelAdmin):
    list_display = (
        "external_id",
        "org",
        "source",
        "subject",
        "assignee",
        "claimed_value",
        "hours",
        "status",
        "fetched_at",
    )
    list_filter = ("org", "source", "status")
    search_fields = ("external_id", "subject")
    autocomplete_fields = ("assignee",)
