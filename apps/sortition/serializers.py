"""DRF serializers for sortition draws (API-first mirror of the Committee flow)."""

from rest_framework import serializers

from apps.orgs.models import WeightWindow

from .models import SortitionDraw


class SortitionDrawSerializer(serializers.ModelSerializer):
    selected = serializers.SerializerMethodField()
    verified = serializers.SerializerMethodField()

    class Meta:
        model = SortitionDraw
        fields = [
            "id",
            "seats",
            "weight_window",
            "seed",
            "result",
            "selected",
            "verified",
            "created_at",
        ]
        read_only_fields = fields

    def get_selected(self, draw):
        return (draw.result or {}).get("selected", [])

    def get_verified(self, draw):
        from . import services

        return services.verify_draw(draw)


class RunDrawSerializer(serializers.Serializer):
    """Validates a draw request: seats, weight window, and a seed."""

    seats = serializers.IntegerField(min_value=1)
    weight_window = serializers.ChoiceField(
        choices=WeightWindow.choices, required=False, allow_blank=True
    )
    seed = serializers.CharField(max_length=128)
