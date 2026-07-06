"""DRF serializers for votes (API-first mirror of the meeting-vote flow)."""

from rest_framework import serializers

from . import services
from .models import Vote


class VoteSerializer(serializers.ModelSerializer):
    status = serializers.SerializerMethodField()

    class Meta:
        model = Vote
        fields = ["id", "question", "options", "status", "opened_at", "closed_at"]
        read_only_fields = fields

    def get_status(self, vote):
        return services.vote_status(vote)


class CreateVoteSerializer(serializers.Serializer):
    """Validates a new vote: a question and 2+ distinct options."""

    question = serializers.CharField(max_length=500)
    options = serializers.ListField(child=serializers.CharField(max_length=255), min_length=2)

    def validate_options(self, value):
        cleaned = [o.strip() for o in value if o.strip()]
        if len(cleaned) < 2:
            raise serializers.ValidationError("At least two non-empty options are required.")
        if len(set(cleaned)) != len(cleaned):
            raise serializers.ValidationError("Options must be distinct.")
        return cleaned


class CastBallotSerializer(serializers.Serializer):
    choice = serializers.CharField(max_length=255)


def tally_dict(t):
    """Serialise a services.Tally to primitive JSON (Decimals as strings)."""
    return {
        "vote_id": t.vote_id,
        "question": t.question,
        "status": t.status,
        "weighted_total": str(t.weighted_total),
        "raw_total": t.raw_total,
        "winner": t.winner,
        "results": [
            {
                "option": r.option,
                "weighted": str(r.weighted),
                "raw": r.raw,
                "weighted_pct": str(r.weighted_pct),
                "raw_pct": str(r.raw_pct),
            }
            for r in t.results
        ],
    }
