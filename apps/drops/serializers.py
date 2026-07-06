"""DRF serializers for drop runs + lines (API-first mirror of the steward flow)."""

from decimal import Decimal

from rest_framework import serializers

from .models import DropLine, DropRun


class DropLineSerializer(serializers.ModelSerializer):
    member = serializers.SerializerMethodField()
    task_ids = serializers.SerializerMethodField()

    class Meta:
        model = DropLine
        fields = [
            "id",
            "membership",
            "member",
            "computed_value",
            "adjustment",
            "adjustment_reason",
            "final_value",
            "task_ids",
        ]
        read_only_fields = fields

    def get_member(self, line):
        return line.membership.user.get_short_name()

    def get_task_ids(self, line):
        return [t.pk for t in line.tasks.all()]


class DropRunSerializer(serializers.ModelSerializer):
    lines = DropLineSerializer(many=True, read_only=True)
    total_final = serializers.SerializerMethodField()

    class Meta:
        model = DropRun
        fields = [
            "id",
            "state",
            "opened_by",
            "opened_at",
            "approved_at",
            "lines",
            "total_final",
        ]
        read_only_fields = fields

    def get_total_final(self, run):
        return sum((line.final_value for line in run.lines.all()), Decimal("0"))


class AdjustLineSerializer(serializers.Serializer):
    """Validates an adjustment; a non-zero adjustment requires a reason (audit trail)."""

    adjustment = serializers.DecimalField(max_digits=16, decimal_places=2)
    adjustment_reason = serializers.CharField(
        max_length=500, required=False, allow_blank=True, default=""
    )

    def validate(self, attrs):
        adjustment = attrs.get("adjustment")
        reason = (attrs.get("adjustment_reason") or "").strip()
        if adjustment and adjustment != Decimal("0") and not reason:
            raise serializers.ValidationError(
                {"adjustment_reason": "A reason is required when an adjustment is applied."}
            )
        return attrs
