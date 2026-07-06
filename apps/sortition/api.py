"""
DRF endpoints for sortition — API-first mirror of the Committee draw.

Routes carry ``org_slug`` so OrgContextMiddleware populates ``request.org`` /
``request.membership`` (same tenancy + membership gate as the HTML pages):

    GET  /api/v1/sortition/orgs/<slug>/draws/            list draws
    POST /api/v1/sortition/orgs/<slug>/draws/            run a seeded draw   (steward)
    GET  /api/v1/sortition/orgs/<slug>/draws/<id>/       retrieve a draw
    GET  /api/v1/sortition/orgs/<slug>/draws/<id>/verify/  reproduce + verify a draw

Running a draw is gated to steward/admin; listing/retrieving/verifying is open to members.
Every queryset is scoped to request.org.
"""

from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from apps.orgs.models import MembershipRole

from . import services
from .models import SortitionDraw
from .serializers import RunDrawSerializer, SortitionDrawSerializer

STEWARD_ROLES = {MembershipRole.STEWARD, MembershipRole.ADMIN}


class IsStewardOrAdmin(permissions.BasePermission):
    """Write actions (running a draw) require a steward/admin membership (or superuser)."""

    message = "Steward or admin role required."

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        user = request.user
        if user.is_authenticated and user.is_superuser:
            return True
        membership = getattr(request, "membership", None)
        return membership is not None and membership.role in STEWARD_ROLES


class SortitionDrawViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SortitionDrawSerializer
    permission_classes = [permissions.IsAuthenticated, IsStewardOrAdmin]

    def get_queryset(self):
        return SortitionDraw.objects.for_org(self.request.org).order_by("-created_at")

    def create(self, request, *args, **kwargs):
        serializer = RunDrawSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        window = (
            serializer.validated_data.get("weight_window")
            or request.org.valuation_config.weight_window
        )
        try:
            draw = services.run_draw(
                request.org,
                serializer.validated_data["seats"],
                window,
                serializer.validated_data["seed"],
            )
        except services.SortitionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SortitionDrawSerializer(draw).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def verify(self, request, *args, **kwargs):
        draw = self.get_object()
        reproduced = services.reproduce(draw)
        stored = (draw.result or {}).get("selected", [])
        return Response(
            {
                "draw_id": draw.pk,
                "verified": reproduced == stored,
                "stored_selected": stored,
                "reproduced_selected": reproduced,
            }
        )


ORG = r"orgs/(?P<org_slug>[-\w]+)"

router = DefaultRouter()
router.register(rf"{ORG}/draws", SortitionDrawViewSet, basename="draw")

urlpatterns = router.urls
