"""
DRF serializers for orgs — the API-first mirror of the onboarding/members UI.

`OnboardingSerializer.create` performs the same atomic Org + ValuationConfig + admin
Membership creation as the onboarding form, keyed on the request user from context.
"""

from __future__ import annotations

from django.db import transaction
from rest_framework import serializers

from .models import (
    BudgetEnforcement,
    BudgetPeriod,
    InviteAudience,
    Membership,
    MembershipRole,
    Org,
    ValuationConfig,
    ValuationMode,
    WeightWindow,
)


class ValuationConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = ValuationConfig
        fields = [
            "valuation_mode",
            "at_risk_multiplier_noncash",
            "at_risk_multiplier_cash",
            "weight_window",
            "assignment_budget_period",
            "assignment_budget_amount",
            "self_assign_cap",
            "budget_enforcement",
        ]


class OrgSerializer(serializers.ModelSerializer):
    valuation_config = ValuationConfigSerializer(read_only=True)
    # The shared-context repo amebo reads (the repo flagged main). Read-only property.
    context_repo = serializers.ReadOnlyField()

    class Meta:
        model = Org
        fields = [
            "slug", "display_name", "unit_name", "default_hourly_rate", "valuation_config",
            "website", "socials", "repos", "context_repo",
        ]


class OnboardingSerializer(serializers.Serializer):
    """Create an org + its valuation config + the caller's admin membership."""

    display_name = serializers.CharField(max_length=255)
    slug = serializers.SlugField(max_length=64)
    unit_name = serializers.CharField(max_length=32, default="points")
    default_hourly_rate = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )

    valuation_mode = serializers.ChoiceField(
        choices=ValuationMode.choices, default=ValuationMode.HOURS_RATE
    )
    at_risk_multiplier_noncash = serializers.DecimalField(
        max_digits=6, decimal_places=3, default="1.0"
    )
    at_risk_multiplier_cash = serializers.DecimalField(
        max_digits=6, decimal_places=3, default="1.0"
    )
    weight_window = serializers.ChoiceField(
        choices=WeightWindow.choices, default=WeightWindow.ALL_TIME
    )
    assignment_budget_period = serializers.ChoiceField(
        choices=BudgetPeriod.choices, default=BudgetPeriod.WEEKLY
    )
    assignment_budget_amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False, allow_null=True
    )
    self_assign_cap = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False, allow_null=True
    )
    budget_enforcement = serializers.ChoiceField(
        choices=BudgetEnforcement.choices, default=BudgetEnforcement.SOFT
    )

    def validate_slug(self, value):
        if Org.objects.filter(slug=value).exists():
            raise serializers.ValidationError("That slug is already taken.")
        return value

    @transaction.atomic
    def create(self, validated_data):
        user = self.context["request"].user
        org = Org.objects.create(
            slug=validated_data["slug"],
            display_name=validated_data["display_name"],
            unit_name=validated_data.get("unit_name") or "points",
            default_hourly_rate=validated_data.get("default_hourly_rate"),
        )
        ValuationConfig.objects.create(
            org=org,
            valuation_mode=validated_data["valuation_mode"],
            at_risk_multiplier_noncash=validated_data["at_risk_multiplier_noncash"],
            at_risk_multiplier_cash=validated_data["at_risk_multiplier_cash"],
            weight_window=validated_data["weight_window"],
            assignment_budget_period=validated_data["assignment_budget_period"],
            assignment_budget_amount=validated_data.get("assignment_budget_amount"),
            self_assign_cap=validated_data.get("self_assign_cap"),
            budget_enforcement=validated_data["budget_enforcement"],
        )
        Membership.objects.create(org=org, user=user, role=MembershipRole.ADMIN)
        return org

    def to_representation(self, instance):
        return OrgSerializer(instance, context=self.context).data


class MembershipSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email", read_only=True)
    effective_rate = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "email", "role", "hourly_rate", "effective_rate"]
        read_only_fields = ["id", "email", "effective_rate"]

    def to_representation(self, instance):
        """L5: a member's pay rate is sensitive. Only expose hourly_rate/effective_rate to
        the member themselves, to org admins, or to superusers — strip it for other members
        so a colleague's rate is not readable across the org."""
        data = super().to_representation(instance)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            # No request context (internal/serialization-only use): do not leak by default.
            data.pop("hourly_rate", None)
            data.pop("effective_rate", None)
            return data
        if user.is_superuser or instance.user_id == user.id:
            return data
        is_admin = Membership.objects.filter(
            org=instance.org, user=user, role=MembershipRole.ADMIN
        ).exists()
        if not is_admin:
            data.pop("hourly_rate", None)
            data.pop("effective_rate", None)
        return data


class InviteSerializer(serializers.Serializer):
    """Mint a magic-link invite (API mirror of the members-page form)."""

    name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    role = serializers.ChoiceField(choices=MembershipRole.choices, default=MembershipRole.MEMBER)
    audience = serializers.ChoiceField(
        choices=InviteAudience.choices, default=InviteAudience.SUPPORTER
    )
    link = serializers.URLField(required=False, allow_blank=True, default="")
    image_url = serializers.URLField(required=False, allow_blank=True, default="")
    drafted_statement = serializers.CharField(required=False, allow_blank=True, default="")
    drafted_social_post = serializers.CharField(required=False, allow_blank=True, default="")
    doorway = serializers.BooleanField(default=False)

    def validate(self, data):
        if not data.get("name") and not data.get("email"):
            raise serializers.ValidationError(
                "Give a name or an email so the invite is attributable."
            )
        return data


class OrgRateSerializer(serializers.Serializer):
    default_hourly_rate = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )
