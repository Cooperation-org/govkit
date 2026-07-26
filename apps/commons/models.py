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
