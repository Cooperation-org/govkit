"""
The commons: what everyone who is invited or signs up can see and do together —
ideas people could coalesce around, before any org exists.

An Idea is a person's own words (title + pitch). Others attach interest:
support (cheer it on) or build (want to work on it). When an idea coalesces,
an org is formed through the normal deliberate paths (founder invite or
operator add-team run) — never auto-created from here.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


class Idea(models.Model):
    title = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, max_length=80)
    pitch = models.TextField(help_text="The poster's own words. Never generated.")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ideas_posted"
    )
    created_at = models.DateTimeField(default=timezone.now)
    # Poster (or an admin) can retire an idea; it drops off the list, history stays.
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)[:70] or "idea"
            slug = base
            n = 2
            while Idea.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)


class VentureInterest(models.Model):
    """One person's standing interest in joining one venture (org).

    The public-side twin of IdeaInterest: made by someone who is NOT a member,
    from the commons or the workers.vc doorway. This row is the ONE home of the
    fact — feeds (the venture's dash rail, the accelerator's oversight list,
    an amebo claw) are views over it and store nothing themselves.

    `responded_at` is the supervision hook: unanswered rows float to the top of
    every feed until someone from the venture marks them replied. No further
    workflow — acceptance happens through the normal invite paths.
    """

    org = models.ForeignKey("orgs.Org", on_delete=models.CASCADE, related_name="interests")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="venture_interests"
    )
    note = models.TextField(
        blank=True, help_text="Optional 'why me', in the person's own words. Never generated."
    )
    created_at = models.DateTimeField(default=timezone.now)
    responded_at = models.DateTimeField(null=True, blank=True)
    responded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="venture_interests_answered",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["org", "user"], name="one_interest_per_user_per_org"),
        ]
        # Unanswered first (Postgres puts NULLs last on a plain ASC), oldest first —
        # the same order every feed shows, so "top of the list" always means
        # "waiting longest with no reply".
        ordering = [models.F("responded_at").asc(nulls_first=True), "created_at"]

    def __str__(self):
        state = "answered" if self.responded_at else "open"
        return f"{self.user.email} → {self.org.slug} ({state})"

    def mark_responded(self, by_user):
        """Idempotent: the first reply wins; a second click changes nothing."""
        if self.responded_at is None:
            self.responded_at = timezone.now()
            self.responded_by = by_user
            self.save(update_fields=["responded_at", "responded_by"])


class IdeaInterestKind(models.TextChoices):
    SUPPORT = "support", "Supports it"
    BUILD = "build", "Wants to build it"


class IdeaInterest(models.Model):
    """One person's standing interest in one idea. Re-declaring updates the kind."""

    idea = models.ForeignKey(Idea, on_delete=models.CASCADE, related_name="interests")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="idea_interests"
    )
    kind = models.CharField(
        max_length=10, choices=IdeaInterestKind.choices, default=IdeaInterestKind.SUPPORT
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["idea", "user"], name="one_interest_per_user_per_idea"),
        ]
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.user.email}: {self.kind} {self.idea.title}"


class SponsorPledge(models.Model):
    """Someone offering to sponsor an org, in their own words.

    The public-side twin of VentureInterest for a person with no account: a
    sponsor is a stranger here until they have given something. workers.vc
    renders the form on /sponsor/ and posts it over S2S; this row is the ONE
    home of the fact, and the attention rail is a view over it.

    A pledge is an intention, never equity. When one is honoured the sponsor
    becomes an ExternalHolder with an OrgStake through the normal deliberate
    path (apps/orgs) — nothing here grants a share.

    `responded_at` is the same supervision hook VentureInterest uses: money
    offered and never answered is the worst thing this table could do, so an
    unanswered pledge floats to the top of the rail until a human replies.
    """

    class Kind(models.TextChoices):
        CASH = "cash", "Cash"
        IN_KIND = "in_kind", "In kind"

    org = models.ForeignKey("orgs.Org", on_delete=models.CASCADE, related_name="sponsor_pledges")

    name = models.CharField(max_length=200)
    email = models.EmailField()
    # Who they are sponsoring as, when that is not themselves.
    org_name = models.CharField(max_length=200, blank=True)

    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.CASH)
    # The tier they clicked, as offered on the page ("silver"), or blank for an
    # amount they typed themselves. Kept as the label they chose rather than
    # derived from the amount: the tier is what was promised to them.
    tier = models.SlugField(max_length=40, blank=True)
    # Null for in-kind, and for a cash pledge with no figure yet. Money, so a
    # decimal — never a float.
    amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    # What they are offering instead of cash, in their words (credits, hosting,
    # legal hours). Free text: a list of ours would only be wrong.
    offer = models.TextField(blank=True)
    note = models.TextField(blank=True, help_text="Their own words. Never generated.")

    # The sponsor page promises a name on it, so consent is asked for, not
    # assumed. Blank `listed_as` means list them by name (or org_name).
    list_publicly = models.BooleanField(default=True)
    listed_as = models.CharField(max_length=200, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    responded_at = models.DateTimeField(null=True, blank=True)
    responded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sponsor_pledges_answered",
    )

    class Meta:
        # Unanswered first, oldest first — the rail's order, same as interests.
        ordering = [models.F("responded_at").asc(nulls_first=True), "created_at"]

    def __str__(self):
        state = "answered" if self.responded_at else "open"
        return f"{self.who} → {self.org.slug} ({self.summary}, {state})"

    @property
    def who(self):
        return f"{self.name} ({self.org_name})" if self.org_name else self.name

    @property
    def public_name(self):
        """How they asked to be listed, or nothing if they asked not to be."""
        if not self.list_publicly:
            return ""
        return self.listed_as or self.org_name or self.name

    @property
    def summary(self):
        """The pledge in a few words, for a rail row or a subject line."""
        if self.kind == self.Kind.IN_KIND:
            return "in kind"
        return f"${self.amount:,.0f}" if self.amount else "an amount to discuss"

    def mark_responded(self, by_user):
        """Idempotent: the first reply wins; a second click changes nothing."""
        if self.responded_at is None:
            self.responded_at = timezone.now()
            self.responded_by = by_user
            self.save(update_fields=["responded_at", "responded_by"])
