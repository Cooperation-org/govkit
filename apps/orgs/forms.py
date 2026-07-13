"""
Forms for the onboarding wizard and the member/roles admin.

These validate and persist the same fields the DRF serializers expose (API-first: the UI
and the API share the model constraints). Onboarding creates the Org + its ValuationConfig
+ the creator's admin Membership atomically.
"""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.db import transaction
from django.utils.text import slugify

from .models import (
    BudgetEnforcement,
    BudgetPeriod,
    InviteAudience,
    Membership,
    MembershipRole,
    OpeningBalance,
    Org,
    ValuationConfig,
    ValuationMode,
    WeightWindow,
)


class OnboardingForm(forms.Form):
    """
    One-flow org setup: identity + starting point visible; valuation mode + rules are
    optional detail (every one has a sane default) shown folded in the UI
    (pattern 19 · DETAILS UNFOLD).
    """

    START_FRESH = "fresh"
    START_EXISTING = "existing"

    display_name = forms.CharField(max_length=255, label="Organization name")
    slug = forms.SlugField(
        max_length=64,
        label="URL slug",
        help_text="Lowercase letters, numbers and hyphens. Used in every org URL.",
    )
    unit_name = forms.CharField(
        max_length=32,
        initial="points",
        label="Value unit",
        help_text='What this org calls a unit of earned value, e.g. "COOK", "points".',
    )

    # Starting point: even an idea has some value. Optional (defaults to fresh) so the
    # API and older clients that never send it keep working.
    start_kind = forms.ChoiceField(
        required=False,
        choices=[
            (START_FRESH, "Fresh start — the pie begins empty"),
            (START_EXISTING, "Existing project — it already has value"),
        ],
        initial=START_FRESH,
        widget=forms.RadioSelect,
        label="Starting point",
    )
    initial_valuation = forms.DecimalField(
        max_digits=16,
        decimal_places=2,
        required=False,
        min_value=Decimal("0.01"),
        label="Initial valuation",
        help_text=(
            "What the project is worth so far, in your value unit. Recorded as your "
            "opening balance; you can split or adjust members' opening balances any "
            "time from Members → import."
        ),
    )
    default_hourly_rate = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        label="Org-wide default hourly rate",
        help_text="Optional. Individual members can override this.",
    )

    valuation_mode = forms.ChoiceField(
        choices=ValuationMode.choices, initial=ValuationMode.HOURS_RATE
    )
    at_risk_multiplier_noncash = forms.DecimalField(
        max_digits=6, decimal_places=3, initial="1.0", label="At-risk multiplier (non-cash)"
    )
    at_risk_multiplier_cash = forms.DecimalField(
        max_digits=6, decimal_places=3, initial="1.0", label="At-risk multiplier (cash)"
    )
    weight_window = forms.ChoiceField(choices=WeightWindow.choices, initial=WeightWindow.ALL_TIME)

    assignment_budget_period = forms.ChoiceField(
        choices=BudgetPeriod.choices, initial=BudgetPeriod.WEEKLY
    )
    assignment_budget_amount = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        required=False,
        label="Assignable value per period",
        help_text="Leave blank for unlimited.",
    )
    self_assign_cap = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        required=False,
        label="Self-assign cap per period",
        help_text="Leave blank for no cap.",
    )
    budget_enforcement = forms.ChoiceField(
        choices=BudgetEnforcement.choices, initial=BudgetEnforcement.SOFT
    )

    def clean_slug(self):
        slug = slugify(self.cleaned_data["slug"])
        if not slug:
            raise forms.ValidationError("Enter a valid slug.")
        if Org.objects.filter(slug=slug).exists():
            raise forms.ValidationError("That slug is already taken.")
        return slug

    def clean(self):
        data = super().clean()
        if not data.get("start_kind"):
            data["start_kind"] = self.START_FRESH
        if data["start_kind"] == self.START_FRESH:
            data["initial_valuation"] = None  # fresh pies begin empty
        return data

    @transaction.atomic
    def save(self, user) -> Org:
        data = self.cleaned_data
        org = Org.objects.create(
            slug=data["slug"],
            display_name=data["display_name"],
            unit_name=data["unit_name"] or "points",
            default_hourly_rate=data.get("default_hourly_rate"),
        )
        ValuationConfig.objects.create(
            org=org,
            valuation_mode=data["valuation_mode"],
            at_risk_multiplier_noncash=data["at_risk_multiplier_noncash"],
            at_risk_multiplier_cash=data["at_risk_multiplier_cash"],
            weight_window=data["weight_window"],
            assignment_budget_period=data["assignment_budget_period"],
            assignment_budget_amount=data.get("assignment_budget_amount"),
            self_assign_cap=data.get("self_assign_cap"),
            budget_enforcement=data["budget_enforcement"],
        )
        membership = Membership.objects.create(org=org, user=user, role=MembershipRole.ADMIN)
        if data.get("initial_valuation"):
            OpeningBalance.objects.create(
                org=org,
                membership=membership,
                value=data["initial_valuation"],
                source_note=(
                    "Initial valuation at setup — credited to the founder; split or "
                    "adjust members' opening balances from Members → import."
                ),
            )
        return org


class InviteForm(forms.Form):
    """
    Admin mints a magic-link invite: who it's for, their audience on the doorway, the
    membership role they get at accept, and the inviter's own drafted words (both draft
    fields start empty — they are the INVITER'S authored text, never generated).
    """

    name = forms.CharField(max_length=255, required=False, label="Invitee name")
    email = forms.EmailField(required=False, label="Email")
    link = forms.URLField(
        required=False,
        assume_scheme="https",
        label="Their link",
        help_text="Optional. Their LinkedIn or website.",
    )
    image_url = forms.URLField(required=False, assume_scheme="https", label="Image URL")
    audience = forms.ChoiceField(
        choices=InviteAudience.choices, initial=InviteAudience.SUPPORTER, label="Audience"
    )
    role = forms.ChoiceField(choices=MembershipRole.choices, initial=MembershipRole.MEMBER)
    drafted_statement = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Drafted commitment statement",
        help_text="Your words, as a starting draft. The invitee edits before committing.",
    )
    drafted_social_post = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Drafted social post",
        help_text="Your words. Queued only with the invitee's consent.",
    )
    doorway = forms.BooleanField(
        required=False, label="Doorway invite (public commitment page first)"
    )

    def clean(self):
        data = super().clean()
        if not data.get("name") and not data.get("email"):
            raise forms.ValidationError("Give a name or an email so the invite is attributable.")
        return data


class MemberUpdateForm(forms.Form):
    """Admin sets a member's role and per-member hourly-rate override."""

    role = forms.ChoiceField(choices=MembershipRole.choices)
    hourly_rate = forms.DecimalField(
        max_digits=12, decimal_places=2, required=False, label="Hourly rate override"
    )


class OrgRateForm(forms.Form):
    """Admin sets the org-wide default hourly rate (Q5a: both rate modes supported)."""

    default_hourly_rate = forms.DecimalField(
        max_digits=12, decimal_places=2, required=False, label="Org-wide default hourly rate"
    )
