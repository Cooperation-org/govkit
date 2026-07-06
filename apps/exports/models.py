"""
Import/export audit.

ImportBatch records each historical-import run (e.g. opening balances from a legacy
system or spreadsheet) so imports are traceable and repeatable. Export is read-only
(no table). The exports agent fills in the actual import/export logic (services + views).
"""

from django.conf import settings
from django.db import models

from apps.orgs.scoping import OrgScoped


class ImportKind(models.TextChoices):
    OPENING_BALANCE = "opening_balance", "Opening balances"


class ImportBatch(OrgScoped):
    kind = models.CharField(
        max_length=32, choices=ImportKind.choices, default=ImportKind.OPENING_BALANCE
    )
    filename = models.CharField(max_length=500, blank=True)
    row_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="import_batches",
    )
    notes = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"ImportBatch({self.get_kind_display()}, {self.org.slug}, {self.row_count} rows)"
