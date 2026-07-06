"""
DRF endpoints for drops — API-first mirror of every steward action.

Routes carry ``org_slug`` so OrgContextMiddleware populates ``request.org`` /
``request.membership`` (it keys on the ``org_slug`` view kwarg), giving these endpoints
the same tenancy + membership gate as the HTML views:

    GET  /api/v1/drops/orgs/<slug>/runs/                list runs
    POST /api/v1/drops/orgs/<slug>/runs/                open a new run   (steward)
    GET  /api/v1/drops/orgs/<slug>/runs/<id>/           retrieve a run
    GET  /api/v1/drops/orgs/<slug>/runs/<id>/review/    review queue
    POST /api/v1/drops/orgs/<slug>/runs/<id>/approve/   approve a run    (steward)
    GET  /api/v1/drops/orgs/<slug>/lines/               list lines
    POST /api/v1/drops/orgs/<slug>/lines/<id>/adjust/   adjust a line    (steward)

Every queryset is scoped to request.org; write actions are gated to steward/admin.
"""

from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from apps.orgs.models import MembershipRole

from . import services
from .models import DropLine, DropRun
from .serializers import AdjustLineSerializer, DropLineSerializer, DropRunSerializer

STEWARD_ROLES = {MembershipRole.STEWARD, MembershipRole.ADMIN}


class IsStewardOrAdmin(permissions.BasePermission):
    """Write actions require a steward/admin membership (or superuser)."""

    message = "Steward or admin role required."

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        user = request.user
        if user.is_authenticated and user.is_superuser:
            return True
        membership = getattr(request, "membership", None)
        return membership is not None and membership.role in STEWARD_ROLES


class OrgScopedMixin:
    """Shared org scoping; request.org is set by OrgContextMiddleware via org_slug."""

    permission_classes = [permissions.IsAuthenticated, IsStewardOrAdmin]


class DropRunViewSet(OrgScopedMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = DropRunSerializer

    def get_queryset(self):
        return (
            DropRun.objects.for_org(self.request.org)
            .prefetch_related("lines__membership__user", "lines__tasks")
            .order_by("-opened_at")
        )

    def create(self, request, *args, **kwargs):
        """Open a new run over the org's eligible done tasks."""
        try:
            run = services.open_run(
                request.org,
                opened_by_membership=getattr(request, "membership", None),
                opened_by_user=request.user,
            )
        except services.NoEligibleTasks as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        serializer = self.get_serializer(run)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def review(self, request, *args, **kwargs):
        run = self.get_object()
        payload = services.review_queue(run)
        return Response(
            {
                "run": DropRunSerializer(run).data,
                "missing_value_task_ids": [t.pk for t in payload["missing_value_tasks"]],
                "total_final": payload["total_final"],
            }
        )

    @action(detail=True, methods=["post"])
    def approve(self, request, *args, **kwargs):
        run = self.get_object()
        try:
            services.approve_run(run, approved_by_user=request.user)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(DropRunSerializer(run).data)


class DropLineViewSet(
    OrgScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    serializer_class = DropLineSerializer

    def get_queryset(self):
        return DropLine.objects.for_org(self.request.org).select_related("run", "membership__user")

    @action(detail=True, methods=["post"])
    def adjust(self, request, *args, **kwargs):
        line = self.get_object()
        if line.run.is_approved:
            return Response(
                {"detail": "Cannot adjust a line after its run is approved."},
                status=status.HTTP_409_CONFLICT,
            )
        serializer = AdjustLineSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.adjust_line(
            line,
            serializer.validated_data["adjustment"],
            serializer.validated_data.get("adjustment_reason", ""),
        )
        line.refresh_from_db()
        return Response(DropLineSerializer(line).data)


ORG = r"orgs/(?P<org_slug>[-\w]+)"

router = DefaultRouter()
router.register(rf"{ORG}/runs", DropRunViewSet, basename="run")
router.register(rf"{ORG}/lines", DropLineViewSet, basename="line")

urlpatterns = router.urls
