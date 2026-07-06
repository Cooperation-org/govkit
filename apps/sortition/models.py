"""
Work-weighted sortition (M2 — model now, views stubbed).

A SortitionDraw records a seeded, work-weighted draw for committee seats. It is auditable:
the same seed over the same weights reproduces the result exactly.
"""

from django.db import models

from apps.orgs.models import WeightWindow
from apps.orgs.scoping import OrgScoped


class SortitionDraw(OrgScoped):
    seats = models.PositiveIntegerField()
    weight_window = models.CharField(
        max_length=20, choices=WeightWindow.choices, default=WeightWindow.ALL_TIME
    )
    seed = models.CharField(
        max_length=128, help_text="Deterministic seed; same seed + weights reproduces the draw."
    )
    # Selected memberships + the weights used, so the draw can be re-verified.
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"SortitionDraw({self.org.slug}, {self.seats} seats)"
