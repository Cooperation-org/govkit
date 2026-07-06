"""
Forms for the onboarding wizard and the member/roles admin.

These validate and persist the same fields the DRF serializers expose (API-first: the UI
and the API share the model constraints). Onboarding creates the Org + its ValuationConfig
+ the creator's admin Membership atomically.
"""

from __future__ import annotations

from django import forms
from django.db import transaction
from django.utils.text import slugify

from .models import (
    BudgetEnforcement,
    BudgetPeriod,
    Membership,
    MembershipRole,
    Org,
    ValuationConfig,
    ValuationMode,
    WeightWindow,
)


class OnboardingForm(forms.Form):
    """One-flow org setup: identity + value unit + valuation mode + rules."""

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
        Membership.objects.create(org=org, user=user, role=MembershipRole.ADMIN)
        return org


class InviteForm(forms.Form):
    """Admin invites a person to an org by email, with a role and optional rate."""

    email = forms.EmailField(label="Email to invite")
    role = forms.ChoiceField(choices=MembershipRole.choices, initial=MembershipRole.MEMBER)
    hourly_rate = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        label="Hourly rate override",
        help_text="Optional. Blank = use the org-wide default rate.",
    )


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
