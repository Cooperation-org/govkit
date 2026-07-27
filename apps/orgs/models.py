"""
Core tenancy + governance-config models.

An Org is the top-level grouping; every domain table is scoped to one (see
apps/orgs/scoping.py). Valuation/governance policy lives in ValuationConfig, membership
(including external identity maps) in Membership, and imported historical equity in
OpeningBalance.

Equity is held by people through Membership, with ONE exception: OrgStake, where the
holder is an ExternalHolder (the outside company that funded the venture). It is
deliberately its own table rather than a nullable user on Membership, so that everything
keyed on a member being a person — drop runs, votes, sortition, exports, provisioning —
keeps working untouched and no fake user account is ever created to stand in for a
company.
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
    """Who the invite addresses on the public doorway (the wall's claim-aspect vocab).

    Four audiences (golda 2026-07-22): advisor/partner retired — advisor was
    mentor with different words, partner was strings-only. Old rows keep their
    stored value; the doorway translates them on display.
    """

    MENTOR = "mentor", "Mentor"
    FUNDER = "funder", "Funder"
    FOUNDER = "founder", "Founder"
    SUPPORTER = "supporter", "Supporter"


class InviteKind(models.TextChoices):
    """
    The three join paths (Golda, 2026-07-24). Each is a distinct destination:

    ORG  — membership: accepting joins you to the invite's org (e.g. a founder
           invited as a co-founder of THIS org). No venture is created.
    POOL — screening: accepting records you in the applicant pool — NO membership,
           NO slices, NO org.
    BYOV — Founder Bringing their Own Venture: accepting creates a NEW venture org
           (the invitee as admin) from the named venture, and lands them on ITS
           dashboard. It does NOT join the inviting org — the venture is its own
           home. The venture fields belong to this path alone.

    Orgs are auto-created only by a BYOV accept naming a real venture, or by an
    operator/kickoff add-team run — never for pool people.
    """

    ORG = "org", "Org membership"
    POOL = "pool", "Applicant pool"
    BYOV = "byov", "BYOV: Founder bringing their own venture"


class InviteStatus(models.TextChoices):
    """Lifecycle: created → (committed →) accepted; revoked kills it at any point."""

    CREATED = "created", "Created"
    COMMITTED = "committed", "Committed"
    ACCEPTED = "accepted", "Accepted"
    REVOKED = "revoked", "Revoked"


class PiePhase(models.TextChoices):
    """Where an org's pie is in its life.

    SETUP — recording the starting split: bets already placed (work done, money at
    risk) before this pie existed. LAUNCHED — earning has started on top; the starting
    numbers stay adjustable until the team locks them in by majority decision.
    LOCKED — the split is the record.
    """

    SETUP = "setup", "Setting up"
    LAUNCHED = "launched", "Launched"
    LOCKED = "locked", "Locked in"


class Cohort(models.Model):
    """
    One run of an accelerator: the teams that went through it together.

    Without this, every venture org ever created piles into one undifferentiated
    list and a second cohort cannot be told from the first. The accelerator is
    itself an Org (it has members, invites, and a pie), so a cohort points at the
    org that runs it rather than duplicating any of that.
    """

    slug = models.SlugField(unique=True, max_length=64)
    name = models.CharField(max_length=255)
    accelerator_org = models.ForeignKey("Org", on_delete=models.PROTECT, related_name="cohorts_run")
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-starts_on", "name"]

    def __str__(self):
        return f"{self.name} ({self.slug})"


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
    # Set when the org starts the genesis curriculum. This, not the presence of
    # rows, is what makes an org "on the path" (apps.orgs.genesis).
    genesis_started_at = models.DateTimeField(null=True, blank=True)
    # The run this team came through, if any. Null for the accelerator itself and
    # for any org that was not part of a cohort.
    cohort = models.ForeignKey(
        Cohort, null=True, blank=True, on_delete=models.SET_NULL, related_name="teams"
    )
    # Public org profile, edited on the org settings page. All optional/additive.
    # The pitch is the team's own words — mission / thesis / what they're building —
    # shown on the venture's public card and page. Never generated.
    pitch = models.TextField(blank=True, help_text="The team's own words. Never generated.")
    website = models.URLField(max_length=1000, blank=True)
    # One line saying what this is, shown under the name and in the link preview.
    # Drafted from the team's own site (apps.orgs.sitepull), theirs to edit.
    tagline = models.CharField(max_length=255, blank=True)
    # The team's own logo and the picture their shared link unfurls with. URLs on
    # the team's own site — we point at them, we don't copy or host them.
    logo_url = models.URLField(max_length=1000, blank=True)
    cover_image_url = models.URLField(max_length=1000, blank=True)
    # What the team needs a person for, as [{"role": str, "detail": str}]. This is
    # the point of the join page: a stranger joins a named need, never an abstraction.
    looking_for = models.JSONField(default=list, blank=True)
    # List of {"label": str, "url": str}.
    socials = models.JSONField(default=list, blank=True)
    # List of {"url": str, "is_main": bool}. The is_main one is the team's shared
    # context repo that amebo reads (see context_repo). Teams bring their own repo.
    repos = models.JSONField(default=list, blank=True)
    # Team calendar (Google Calendar / iCal share URL) and team chat (Slack/Discord
    # invite). When set, the cohort top bar shows a Calendar / Chat link.
    calendar_url = models.URLField(max_length=1000, blank=True)
    chat_url = models.URLField(max_length=1000, blank=True)
    # A team whose equity record lives in another system (Slicing Pie's Pie Slicer today)
    # points at it here. This is a POINTER, never a sync: GovKit reads nothing from it and
    # writes nothing to it, so nothing here can overwrite what GovKit computed. `pie_as_of`
    # is the date that outside record was last brought up to date; work approved in GovKit
    # after it is what the team still owes the outside pie.
    pie_url = models.URLField(max_length=1000, blank=True)
    pie_as_of = models.DateField(null=True, blank=True)
    # The pie moves through three phases. SETUP: the team records its starting split —
    # work already done and money already put at risk before this pie existed. A venture
    # usually arrives as several people, so this is not one sitting — they invite, grant,
    # invite again. LAUNCHED: earning starts on top of the starting split, but the
    # starting numbers stay adjustable — nothing is final until the team locks it in by
    # majority decision (a work-weighted vote; money doesn't vote). LOCKED: the split is
    # the record. The vote itself lives in apps.pie (PieLockVote).
    pie_phase = models.CharField(max_length=12, choices=PiePhase.choices, default="setup")
    pie_locked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_name"]

    def __str__(self):
        return f"{self.display_name} ({self.slug})"

    @property
    def context_repo(self) -> str:
        """The repo amebo reads for shared context: the one flagged main, else the first."""
        repos = self.repos or []
        for repo in repos:
            if isinstance(repo, dict) and repo.get("is_main") and repo.get("url"):
                return repo["url"]
        for repo in repos:
            if isinstance(repo, dict) and repo.get("url"):
                return repo["url"]
        return ""

    @property
    def asks(self) -> list:
        """What the team is looking for, cleaned: [{"role", "detail"}] with a role."""
        out = []
        for ask in self.looking_for or []:
            if isinstance(ask, dict) and (ask.get("role") or "").strip():
                out.append(
                    {
                        "role": ask["role"].strip(),
                        "detail": (ask.get("detail") or "").strip(),
                    }
                )
        return out

    def profile_checklist(self) -> list:
        """The team profile as things to do, each done or not.

        Positive on purpose: a team looking at their own page should see what
        they have and what is left, not a list of what is wrong with them.
        Order is the order of the fields on the settings page.
        """
        return [
            ("Your website", bool(self.website)),
            ("One line saying what this is", bool(self.tagline.strip())),
            ("What you are building", bool(self.pitch.strip())),
            ("Who you are looking for", bool(self.asks)),
            ("A logo", bool(self.logo_url)),
            ("A picture for your link", bool(self.cover_image_url)),
        ]

    @property
    def profile_ready(self) -> bool:
        """True once every part of the profile is filled in."""
        return all(done for _label, done in self.profile_checklist())


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
    # ORG joins you to `org` on accept; POOL only screens you into the applicant pool
    # (no membership, no slices, no org created). See InviteKind.
    kind = models.CharField(max_length=10, choices=InviteKind.choices, default=InviteKind.ORG)

    # Who the invite is for (display/personalization; email is not a hard gate — an
    # OAuth identity may carry a different verified email than the one invited).
    name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    link = models.URLField(max_length=1000, blank=True, help_text="Their LinkedIn or website.")
    image_url = models.URLField(max_length=1000, blank=True)

    # The founder's venture (founder-audience invites): names the thing they are
    # launching, so the doorway can center THEIR venture on cards and pages.
    venture_name = models.CharField(max_length=255, blank=True)
    venture_url = models.URLField(max_length=1000, blank=True)

    # An invite carries no money terms. What a venture starts with is the founder's to
    # set, in their own org, after they accept — see Org.pie_phase (the setup phase).

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
    video_url = models.URLField(max_length=1000, blank=True)

    # Who accepted (set at accept for every kind). For POOL invites this row IS the
    # screened-applicant state: accepted + accepted_by, with no Membership anywhere.
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invites_accepted",
    )

    # When the accept happened (the attention rail's awareness read). Null on
    # rows accepted before this field existed — they simply predate the feed.
    accepted_at = models.DateTimeField(null=True, blank=True)
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

    def save(self, *args, **kwargs):
        """A founder bringing their own venture is always its Admin — they own it.

        The rule lives here, not in the form, so every way an invite is minted (the
        members page, the S2S API, mint_invite, the Django admin) records the same
        thing the accept then honors.
        """
        if self.kind == InviteKind.BYOV:
            self.role = MembershipRole.ADMIN
        return super().save(*args, **kwargs)

    @property
    def status_label(self) -> str:
        """Status for display; flags expiry on the still-open states."""
        label = self.get_status_display()
        if self.is_expired and self.status in (InviteStatus.CREATED, InviteStatus.COMMITTED):
            label = f"{label} (expired)"
        return label

    def mark_committed(
        self, claim_id: int | None = None, statement_as_published: str = "", video_url: str = ""
    ):
        """created → committed (idempotent; no-op if already committed/accepted).

        claim_id may be None for the 'already committed' special case: the person's
        attestation already exists on the wall (or is being skipped for a demo org),
        so no new claim is written and none is linked."""
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

    def mark_accepted(self, by=None):
        self.status = InviteStatus.ACCEPTED
        self.accepted_by = by
        self.accepted_at = timezone.now()
        self.save(update_fields=["status", "accepted_by", "accepted_at"])

    def mark_revoked(self):
        """Kill the link at any pre-accept point (no-op once accepted — the join stands)."""
        if self.status == InviteStatus.ACCEPTED:
            return
        self.status = InviteStatus.REVOKED
        self.save(update_fields=["status"])


class ChecklistAction(models.TextChoices):
    TICK = "tick", "Ticked"
    UNTICK = "untick", "Unticked"


class ChecklistEvent(models.Model):
    """
    One thing that happened on a venture org's genesis checklist. APPEND-ONLY:
    unchecking writes an untick, it never deletes or nulls a tick, so the record
    of having done something survives being undone.

    The curriculum itself is NOT stored here — it lives once, in
    apps.orgs.genesis.MODULES, and is joined to these events by item_key at render
    time. That is why editing the curriculum reaches every org at once and why
    there is nothing to reseed.

    title_shown is the item's wording at the moment it was acted on: the honest
    version stamp, and what lets a retired item still describe itself.
    """

    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="checklist_events")
    # Joins to apps.orgs.genesis.ITEM_INDEX. Keys are permanent; see that module.
    item_key = models.CharField(max_length=64)
    action = models.CharField(max_length=10, choices=ChecklistAction.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="checklist_events",
    )
    at = models.DateTimeField(default=timezone.now)
    title_shown = models.CharField(max_length=255)

    class Meta:
        ordering = ["at", "id"]
        indexes = [
            models.Index(fields=["org", "item_key", "at"]),
            models.Index(fields=["org", "at"]),
        ]

    def __str__(self):
        return f"ChecklistEvent({self.org.slug}/{self.item_key}: {self.action})"


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


class ExternalHolder(models.Model):
    """An outside company that can hold equity in an org without being one.

    The sponsor of a venture is typically NOT a tenant here: LinkedTrust funds ventures
    in the cohort but does not run its pie in GovKit, has no members here, and must
    never appear in an org list, a cohort, or the provisioning chain. Making it an Org
    to give it a stake would put it in all of them.

    It is a real row rather than a name typed onto each stake so that one company
    holding stakes in several ventures is one identity, and so renaming it is one edit.

    Its OWN equity is not modelled here and must not be. LinkedTrust's cap table lives
    on Fairmint and is closed to new people; GovKit holds a link to it and nothing more,
    the same way it references any fact whose home is elsewhere. What GovKit computes is
    only what this holder owns of a venture INSIDE GovKit.
    """

    slug = models.SlugField(unique=True, max_length=64)
    display_name = models.CharField(max_length=255)
    url = models.URLField(
        max_length=1000,
        blank=True,
        help_text="Where this company's own equity actually lives, e.g. its Fairmint cap "
        "table. A reference for people to follow. GovKit never reads or computes it.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_name"]

    def __str__(self):
        return f"{self.display_name} ({self.slug})"


class OrgStake(models.Model):
    """Equity in `org` held by an ExternalHolder — the sponsor case, not a person.

    A company that put cash and an idea into a venture holds part of that venture's
    pie, but it has no user account and never should have one: a company standing in
    the members list as a fake person would leak into drop runs, votes, provisioning
    and exports, and would be a lie in every one of them.

    So this is its own table, read only by the pie. Two consequences worth knowing:

    * It is ECONOMIC, NOT GOVERNANCE. Voting weight comes from memberships
      (apps/orgs/weighting.py) and is untouched by this, so a sponsor's share never
      votes. An outside company has no one here to cast a ballot.
    * It DILUTES like everything else. The pie is a sum, so each drop line a member
      earns grows the total and shrinks the sponsor's percentage, with no floor.

    Rows are additive, like OpeningBalance: a second grant is a second row, and the
    holder's stake is their sum, each with its own note about where it came from.

    A stake carries either a fixed ``value`` or, before the pie is locked in, a
    ``target_pct``: an agreed share of the STARTING split ("they hold 10% at the
    start"). A percent stake has no fixed value while the pie is still adjustable —
    the pie resolves it against the whole starting set every time it is computed, so
    the order the team enters things in never matters. Locking in writes the resolved
    value and keeps the percent as provenance. After lock, a percent grant is minted
    as new equity instead (apps.pie.services.value_for_target_share).
    """

    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="org_stakes")
    holder = models.ForeignKey(ExternalHolder, on_delete=models.PROTECT, related_name="stakes")
    value = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    # Share of the starting split (0–100), for stakes agreed as a percentage.
    target_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    source_note = models.CharField(max_length=500, blank=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="org_stakes_granted",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # A stake must say what it is: a fixed value, a percent of the starting
            # split, or (once locked in) both — never neither.
            models.CheckConstraint(
                check=models.Q(value__isnull=False) | models.Q(target_pct__isnull=False),
                name="orgstake_value_or_pct",
            ),
        ]

    def __str__(self):
        amount = self.value if self.value is not None else f"{self.target_pct}%"
        return f"OrgStake({self.holder.slug} in {self.org.slug}, {amount})"
