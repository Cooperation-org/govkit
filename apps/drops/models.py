"""
Drop runs — the immutable, auditable earnings engine.

Open a run -> review queue of tasks -> per-line adjust with a required reason ->
approve -> lines become issued equity. Once a run is approved its lines are frozen.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.orgs.scoping import OrgScoped


class DropRunState(models.TextChoices):
    OPEN = "open", "Open"
    APPROVED = "approved", "Approved"


class DropRun(OrgScoped):
    opened_by = models.ForeignKey(
        "orgs.Membership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="opened_drop_runs",
    )
    # Kept for provenance even if the membership row is later removed.
    opened_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="opened_drop_runs",
    )
    state = models.CharField(max_length=16, choices=DropRunState.choices, default=DropRunState.OPEN)
    opened_at = models.DateTimeField(auto_now_add=True)
    # Who approved the run (audit trail). Nullable so a superuser (no membership) can
    # approve, and to keep the row if the membership is later removed.
    approved_by = models.ForeignKey(
        "orgs.Membership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_drop_runs",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-opened_at"]

    def __str__(self):
        return f"DropRun #{self.pk} ({self.org.slug}, {self.state})"

    @property
    def is_approved(self):
        return self.state == DropRunState.APPROVED


class DropLine(OrgScoped):
    """One member's earnings within a run, traceable to the tasks that produced it."""

    run = models.ForeignKey(DropRun, on_delete=models.CASCADE, related_name="lines")
    membership = models.ForeignKey(
        "orgs.Membership", on_delete=models.PROTECT, related_name="drop_lines"
    )
    # Traceability: which tasks this line was computed from.
    tasks = models.ManyToManyField("tasksources.TrackedTask", blank=True, related_name="drop_lines")

    computed_value = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    adjustment = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    adjustment_reason = models.CharField(max_length=500, blank=True)
    # Who adjusted this line and when (audit trail). Set only alongside a non-zero
    # adjustment + reason; cleared when the adjustment is reset to zero.
    adjusted_by = models.ForeignKey(
        "orgs.Membership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="adjusted_drop_lines",
    )
    adjusted_at = models.DateTimeField(null=True, blank=True)
    final_value = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))

    class Meta:
        ordering = ["run", "membership"]

    def __str__(self):
        return f"DropLine({self.membership}, {self.final_value})"

    def clean(self):
        # A non-zero adjustment must carry a reason (audit trail).
        if (
            self.adjustment
            and self.adjustment != Decimal("0")
            and not self.adjustment_reason.strip()
        ):
            raise ValidationError(
                {"adjustment_reason": "A reason is required when an adjustment is applied."}
            )

    def save(self, *args, **kwargs):
        # Immutable once the run is approved.
        if self.pk is not None:
            existing = DropLine.objects.filter(pk=self.pk).select_related("run").first()
            if existing and existing.run.is_approved:
                raise ValidationError("Cannot modify a drop line after its run is approved.")
        self.full_clean(exclude=None, validate_unique=False)
        super().save(*args, **kwargs)
