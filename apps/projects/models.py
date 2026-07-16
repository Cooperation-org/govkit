"""
Projects tracker — an OPTIONAL portfolio layer over the org's work.

A Project is the org-level record of one effort: what kind it is (internal, campaign,
lead, client), where its pieces live (links — the task board, CRM campaign, docs; each
fact keeps its one home, we only reference), and — for paid work — the deal: budget,
promised member splits, and payouts. Paid-out and remaining are always computed from
Payout rows (OpenProject-style), never typed into a field.

This app is optional: orgs that don't create projects never see it. It owns no
governance state and nothing else in GovKit depends on it.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.orgs.scoping import OrgScoped


class ProjectKind(models.TextChoices):
    """The shapes a project can take; each kind carries different expectations.

    internal — team-facing or core-tech foundational work.
    campaign — trying to accomplish something / get attention in the world.
    lead     — trying to land an RFP or contract.
    client   — active paid work: a deal with budget, splits, and payouts.
    """

    INTERNAL = "internal", "Internal"
    CAMPAIGN = "campaign", "Campaign"
    LEAD = "lead", "Lead"
    CLIENT = "client", "Client"


class ProjectStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    DONE = "done", "Done"
    ARCHIVED = "archived", "Archived"


class Project(OrgScoped):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=80)
    kind = models.CharField(max_length=16, choices=ProjectKind.choices)
    status = models.CharField(
        max_length=16, choices=ProjectStatus.choices, default=ProjectStatus.ACTIVE
    )
    summary = models.TextField(blank=True)
    lead = models.ForeignKey(
        "orgs.Membership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="led_projects",
    )
    due = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["org", "slug"], name="uniq_project_org_slug"),
        ]

    def __str__(self):
        return f"{self.name} ({self.org.slug}, {self.kind})"


class ProjectLink(OrgScoped):
    """A pointer to one of the project's pieces, wherever it lives.

    The label carries the meaning ("Taiga board", "CRM campaign", "MAIN.md",
    "Proposal doc", "Live site"); ref is a URL or a repo-relative path.
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="links")
    label = models.CharField(max_length=80)
    ref = models.CharField(max_length=500)

    class Meta:
        ordering = ["label"]
        constraints = [
            models.UniqueConstraint(fields=["project", "label"], name="uniq_link_project_label"),
        ]

    def __str__(self):
        return f"{self.project.slug}: {self.label}"


class Deal(OrgScoped):
    """The agreed terms of a paid project: the budget ceiling and its provenance.

    One deal per project. The promised member shares live in Split rows; money that
    actually went out lives in Payout rows. Nothing here is ever recomputed by hand.
    """

    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name="deal")
    budget_total = models.DecimalField(max_digits=16, decimal_places=2)
    currency = models.CharField(max_length=8, default="USD")
    agreed_on = models.DateField(null=True, blank=True)
    # Where the terms were agreed — a doc URL or message link, so the number is auditable.
    source_ref = models.CharField(max_length=500, blank=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"Deal for {self.project.slug}: {self.budget_total} {self.currency}"


class Split(OrgScoped):
    """A promised share of the deal's budget to one member, in percent."""

    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, related_name="splits")
    membership = models.ForeignKey(
        "orgs.Membership", on_delete=models.PROTECT, related_name="project_splits"
    )
    percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )

    class Meta:
        ordering = ["-percent"]
        constraints = [
            models.UniqueConstraint(fields=["deal", "membership"], name="uniq_split_deal_member"),
        ]

    def __str__(self):
        return f"{self.membership.user} {self.percent}% of {self.deal.project.slug}"


def validate_split_total(deal, splits_percents):
    """Reject a set of splits that promises more than 100% of the budget."""
    total = sum(splits_percents)
    if total > 100:
        raise ValidationError(f"Splits total {total}% — cannot promise more than 100%.")


class Payout(OrgScoped):
    """Money actually paid out to a member against a project.

    The project's paid-out figure is the sum of these rows — the row is the fact.
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="payouts")
    membership = models.ForeignKey(
        "orgs.Membership", on_delete=models.PROTECT, related_name="project_payouts"
    )
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    paid_on = models.DateField()
    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_payouts",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-paid_on", "-created_at"]

    def __str__(self):
        return f"{self.amount} to {self.membership.user} ({self.project.slug})"
