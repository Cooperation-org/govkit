from django.contrib import admin

from .models import ImportBatch


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "org", "kind", "filename", "row_count", "created_by", "created_at")
    list_filter = ("org", "kind")
