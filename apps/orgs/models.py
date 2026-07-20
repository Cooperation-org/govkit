"""
Core tenancy + governance-config models.

An Org is the top-level grouping; every domain table is scoped to one (see
apps/orgs/scoping.py). Valuation/governance policy lives in ValuationConfig, membership
(including external identity maps) in Membership, and imported historical equity in
OpeningBalance.
"""

import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


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


class InviteAudience(models.TextChoices):
    """Who the invite addresses on the public doorway (the wall's claim-aspect vocab)."""

    ADVISOR = "advisor", "Advisor"
    MENTOR = "mentor", "Mentor"
    PARTNER = "partner", "Partner"
    FUNDER = "funder", "Funder"
    FOUNDER = "founder", "Founder"
    SUPPORTER = "supporter", "Supporter"


class InviteStatus(models.TextChoices):
    """Lifecycle: created → (committed →) accepted; revoked kills it at any point."""

    CREATED = "created", "Created"
    COMMITTED = "committed", "Committed"
    ACCEPTED = "accepted", "Accepted"
    REVOKED = "revoked", "Revoked"


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


def new_invite_code() -> str:
    """Opaque, URL-safe invite code (the whole magic link is this code)."""
    return secrets.token_urlsafe(16)


def default_invite_expiry():
    return timezone.now() + timedelta(days=30)


class Invite(models.Model):
    """
    A single-use, stateful org invite addressed by an opaque code (magic link).

    Two-step ("doorway") invites route through a public commitment page first; the
    doorway resolves the code via the S2S API, posts back the LinkedTrust claim id on
    commit, and then sends the invitee to the accept URL (SSO → Membership → dashboard).
    Direct invites skip straight to accept — they simply never pass through `committed`.

    The drafted_statement / drafted_social_post fields hold the INVITER'S authored words
    (empty unless she wrote them) — never system-generated; the invitee edits before
    committing. An invite is dead once accepted, revoked, or expired.
    """

    code = models.CharField(max_length=32, unique=True, default=new_invite_code)
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="invites")
    role = models.CharField(
        max_length=20, choices=MembershipRole.choices, default=MembershipRole.MEMBER
    )
    audience = models.CharField(
        max_length=20, choices=InviteAudience.choices, default=InviteAudience.SUPPORTER
    )

    # Who the invite is for (display/personalization; email is not a hard gate — an
    # OAuth identity may carry a different verified email than the one invited).
    name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    link = models.URLField(blank=True, help_text="Their LinkedIn or website.")
    image_url = models.URLField(blank=True)

    # The founder's venture (founder-audience invites): names the thing they are
    # launching, so the doorway can center THEIR venture on cards and pages.
    venture_name = models.CharField(max_length=255, blank=True)
    venture_url = models.URLField(blank=True)

    # The inviter's authored drafts (never generated); invitee edits before commit.
    drafted_statement = models.TextField(blank=True)
    drafted_social_post = models.TextField(blank=True)

    # Doorway invites route through the public commitment page first; direct invites
    # go straight to accept. Persisted so the share link can be rebuilt any time.
    doorway = models.BooleanField(default=False)

    status = models.CharField(
        max_length=20, choices=InviteStatus.choices, default=InviteStatus.CREATED
    )
    committed_claim_id = models.IntegerField(null=True, blank=True)
    statement_as_published = models.TextField(blank=True)
    video_url = models.URLField(blank=True)

    expires_at = models.DateTimeField(default=default_invite_expiry)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invites_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        who = self.name or self.email or self.code
        return f"Invite({who} → {self.org.slug}, {self.status})"

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def can_accept(self) -> bool:
        """Accept is allowed from created OR committed (direct invites skip committed)."""
        return self.status in (InviteStatus.CREATED, InviteStatus.COMMITTED) and not self.is_expired

    @property
    def status_label(self) -> str:
        """Status for display; flags expiry on the still-open states."""
        label = self.get_status_display()
        if self.is_expired and self.status in (InviteStatus.CREATED, InviteStatus.COMMITTED):
            label = f"{label} (expired)"
        return label

    def mark_committed(self, claim_id: int, statement_as_published: str = "", video_url: str = ""):
        """created → committed (idempotent; no-op if already committed/accepted)."""
        if self.status != InviteStatus.CREATED:
            return
        self.status = InviteStatus.COMMITTED
        self.committed_claim_id = claim_id
        self.statement_as_published = statement_as_published
        self.video_url = video_url
        self.save(
            update_fields=[
                "status",
                "committed_claim_id",
                "statement_as_published",
                "video_url",
            ]
        )

    def mark_accepted(self):
        self.status = InviteStatus.ACCEPTED
        self.save(update_fields=["status"])

    def mark_revoked(self):
        """Kill the link at any pre-accept point (no-op once accepted — the join stands)."""
        if self.status == InviteStatus.ACCEPTED:
            return
        self.status = InviteStatus.REVOKED
        self.save(update_fields=["status"])


class ChecklistItem(models.Model):
    """
    One item of a venture org's genesis checklist (the any-order onboarding path).
    Grouped into modules by apps.orgs.genesis; done is a timestamp + who, so the
    checklist doubles as a plain record of what happened when.
    """

    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="checklist_items")
    module = models.CharField(max_length=32)
    title = models.CharField(max_length=255)
    order = models.PositiveIntegerField(default=0)
    done_at = models.DateTimeField(null=True, blank=True)
    done_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="checklist_items_done",
    )

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        state = "done" if self.done_at else "open"
        return f"ChecklistItem({self.org.slug}/{self.module}: {self.title[:40]}, {state})"


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
