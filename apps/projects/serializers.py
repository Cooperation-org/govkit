"""Serializers for the projects tracker API."""

from rest_framework import serializers

from apps.orgs.models import Membership

from .models import Deal, Payout, Project, ProjectLink, Split


class ProjectLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectLink
        fields = ["id", "label", "ref"]


class SplitSerializer(serializers.ModelSerializer):
    member = serializers.CharField(source="membership.user.username", read_only=True)

    class Meta:
        model = Split
        fields = ["id", "membership", "member", "percent"]

    def validate_membership(self, membership):
        org = self.context["org"]
        if membership.org_id != org.id:
            raise serializers.ValidationError("Membership belongs to a different org.")
        return membership


class DealSerializer(serializers.ModelSerializer):
    splits = SplitSerializer(many=True, required=False)

    class Meta:
        model = Deal
        fields = ["budget_total", "currency", "agreed_on", "source_ref", "notes", "splits"]

    def validate(self, attrs):
        splits = attrs.get("splits", [])
        total = sum(s["percent"] for s in splits)
        if total > 100:
            raise serializers.ValidationError(
                f"Splits total {total}% — cannot promise more than 100%."
            )
        memberships = [s["membership"] for s in splits]
        if len(memberships) != len(set(m.id for m in memberships)):
            raise serializers.ValidationError("Each member may appear in the splits only once.")
        return attrs


class PayoutSerializer(serializers.ModelSerializer):
    member = serializers.CharField(source="membership.user.username", read_only=True)

    class Meta:
        model = Payout
        fields = ["id", "membership", "member", "amount", "paid_on", "note"]

    def validate_membership(self, membership):
        org = self.context["org"]
        if membership.org_id != org.id:
            raise serializers.ValidationError("Membership belongs to a different org.")
        return membership

    def validate_amount(self, amount):
        if amount <= 0:
            raise serializers.ValidationError("Payout amount must be positive.")
        return amount


class ProjectSerializer(serializers.ModelSerializer):
    links = ProjectLinkSerializer(many=True, read_only=True)
    lead_name = serializers.CharField(source="lead.user.username", read_only=True, default=None)

    class Meta:
        model = Project
        fields = [
            "id",
            "name",
            "slug",
            "kind",
            "status",
            "summary",
            "lead",
            "lead_name",
            "due",
            "links",
            "created_at",
        ]
        read_only_fields = ["created_at"]

    def validate_lead(self, lead):
        if lead is None:
            return lead
        org = self.context["org"]
        if lead.org_id != org.id:
            raise serializers.ValidationError("Lead membership belongs to a different org.")
        return lead
