"""
Core tenancy + governance-config models.

An Org is the top-level grouping; every domain table is scoped to one (see
apps/orgs/scoping.py). Valuation/governance policy lives in ValuationConfig, membership
(including external identity maps) in Membership, and imported historical equity in
OpeningBalance.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models


# --- Choice vocabularies (shared across apps) ---
class ValuationMode(models.TextChoices):
    HOURS_RATE = "hours_rate", "Hours x rate"
    DIRECT_VALUE = "direct_value", "Direct value tags"


class WeightWindow(models.TextChoices):
    ALL_TIME = "all_time", "All time"
    TRAILING_12M = "trailing_12m", "Trailing 12 months"


class BudgetPeriod(models.TextChoices):
    WEEKLY = "weekly", "Weekly"
    MONTHLY = "monthly", "Monthly"
    QUARTERLY = "quarterly", "Quarterly"


class BudgetEnforcement(models.TextChoices):
    SOFT = "soft", "Soft (warn on exceed)"
    HARD = "hard", "Hard (block on exceed)"


class MembershipRole(models.TextChoices):
    ADMIN = "admin", "Admin"
    STEWARD = "steward", "Steward"
    MEMBER = "member", "Member"


class Org(models.Model):
    """A tenant. Self-hosters run one; we run many."""

    slug = models.SlugField(unique=True, max_length=64)
    display_name = models.CharField(max_length=255)
    # What this org calls a unit of earned value: "COOK", "slices", "points", "$".
    unit_name = models.CharField(max_length=32, default="points")
    # Optional org-wide default rate; a Membership may override per person.
    default_hourly_rate = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_name"]

    def __str__(self):
        return f"{self.display_name} ({self.slug})"


class ValuationConfig(models.Model):
    """Per-org valuation + governance policy. One row per Org."""

    org = models.OneToOneField(Org, on_delete=models.CASCADE, related_name="valuation_config")

    valuation_mode = models.CharField(
        max_length=20, choices=ValuationMode.choices, default=ValuationMode.HOURS_RATE
    )
    # At-risk multipliers (Slicing Pie uses 2x non-cash / 4x cash; default 1.0 = off).
    at_risk_multiplier_noncash = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal("1.0")
    )
    at_risk_multiplier_cash = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal("1.0")
    )
    weight_window = models.CharField(
        max_length=20, choices=WeightWindow.choices, default=WeightWindow.ALL_TIME
    )

    # Assignment-budget policy. null amount = unlimited (a valid config).
    assignment_budget_period = models.CharField(
        max_length=20, choices=BudgetPeriod.choices, default=BudgetPeriod.WEEKLY
    )
    assignment_budget_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Assignable value per period. Null = unlimited.",
    )
    self_assign_cap = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Max value a member may self-assign per period. Null = no cap.",
    )
    budget_enforcement = models.CharField(
        max_length=10, choices=BudgetEnforcement.choices, default=BudgetEnforcement.SOFT
    )

    def __str__(self):
        return f"ValuationConfig({self.org.slug})"


class Membership(models.Model):
    """A user's place in an org, plus their external-tracker identity map."""

    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(
        max_length=20, choices=MembershipRole.choices, default=MembershipRole.MEMBER
    )
    hourly_rate = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Overrides Org.default_hourly_rate for this person. Null = use org default.",
    )

    # External identity map — explicit, never inferred (BOUNDARIES principle).
    taiga_username = models.CharField(max_length=255, blank=True)
    taiga_user_id = models.IntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["org", "user"], name="uniq_membership_org_user"),
        ]
        ordering = ["org", "user"]

    def __str__(self):
        return f"{self.user} @ {self.org.slug} ({self.role})"

    @property
    def effective_rate(self):
        """Per-member rate if set, else the org default (may be None)."""
        return self.hourly_rate if self.hourly_rate is not None else self.org.default_hourly_rate


class OpeningBalance(models.Model):
    """Imported pre-existing equity for a member (the historical-import target)."""

    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="opening_balances")
    membership = models.ForeignKey(
        Membership, on_delete=models.CASCADE, related_name="opening_balances"
    )
    value = models.DecimalField(max_digits=16, decimal_places=2)
    source_note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"OpeningBalance({self.membership}, {self.value})"
