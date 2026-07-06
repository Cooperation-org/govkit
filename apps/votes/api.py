"""
DRF endpoints for votes — API-first mirror of every meeting-vote action.

Routes carry ``org_slug`` so OrgContextMiddleware populates ``request.org`` /
``request.membership`` (same tenancy + membership gate as the HTML pages):

    GET  /api/v1/votes/orgs/<slug>/votes/               list votes
    POST /api/v1/votes/orgs/<slug>/votes/               create a draft vote   (steward)
    GET  /api/v1/votes/orgs/<slug>/votes/<id>/          retrieve a vote
    POST /api/v1/votes/orgs/<slug>/votes/<id>/open/     open + snapshot weights(steward)
    POST /api/v1/votes/orgs/<slug>/votes/<id>/vote/     cast/replace a ballot  (member)
    POST /api/v1/votes/orgs/<slug>/votes/<id>/close/    close a vote           (steward)
    GET  /api/v1/votes/orgs/<slug>/votes/<id>/tally/    weighted + raw tally

Creation/open/close are gated to steward/admin; casting a ballot is open to any member.
Every queryset is scoped to request.org.
"""

from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from apps.orgs.models import MembershipRole

from . import services
from .models import Vote
from .serializers import (
    CastBallotSerializer,
    CreateVoteSerializer,
    VoteSerializer,
    tally_dict,
)

STEWARD_ROLES = {MembershipRole.STEWARD, MembershipRole.ADMIN}
# Actions that require a steward/admin. `vote` (casting a ballot) is open to any member.
STEWARD_ACTIONS = {"create", "open", "close", "destroy"}


class VotePermission(permissions.BasePermission):
    """Steward/admin for lifecycle actions; any authenticated member for the rest."""

    message = "Steward or admin role required."

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if view.action not in STEWARD_ACTIONS:
            return True
        if request.user.is_superuser:
            return True
        membership = getattr(request, "membership", None)
        return membership is not None and membership.role in STEWARD_ROLES


class VoteViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = VoteSerializer
    permission_classes = [permissions.IsAuthenticated, VotePermission]

    def get_queryset(self):
        # No ballot prefetch: casting then tallying in one request must read fresh ballots,
        # and the tally does its own query anyway.
        return Vote.objects.for_org(self.request.org)

    def create(self, request, *args, **kwargs):
        serializer = CreateVoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            vote = services.create_vote(
                request.org,
                serializer.validated_data["question"],
                serializer.validated_data["options"],
            )
        except services.VoteError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(VoteSerializer(vote).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def open(self, request, *args, **kwargs):
        vote = self.get_object()
        try:
            services.open_vote(vote)
        except services.VoteError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(VoteSerializer(vote).data)

    @action(detail=True, methods=["post"])
    def vote(self, request, *args, **kwargs):
        vote = self.get_object()
        membership = getattr(request, "membership", None)
        if membership is None:
            return Response(
                {"detail": "Only a member of this org can vote."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = CastBallotSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            services.cast_ballot(vote, membership, serializer.validated_data["choice"])
        except services.VoteError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(tally_dict(services.tally(vote)))

    @action(detail=True, methods=["post"])
    def close(self, request, *args, **kwargs):
        vote = self.get_object()
        try:
            services.close_vote(vote)
        except services.VoteError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(VoteSerializer(vote).data)

    @action(detail=True, methods=["get"])
    def tally(self, request, *args, **kwargs):
        vote = self.get_object()
        return Response(tally_dict(services.tally(vote)))


ORG = r"orgs/(?P<org_slug>[-\w]+)"

router = DefaultRouter()
router.register(rf"{ORG}/votes", VoteViewSet, basename="vote")

urlpatterns = router.urls
