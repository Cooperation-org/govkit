"""
DRF endpoints for the projects tracker — API-first so amebo and Marten drive it.

Routes carry ``org_slug`` so OrgContextMiddleware populates ``request.org`` /
``request.membership`` (same tenancy + membership gate as the HTML views):

    GET    /api/v1/projects/orgs/<slug>/portfolio/                whole-org money picture
    GET    /api/v1/projects/orgs/<slug>/projects/                 list projects
    POST   /api/v1/projects/orgs/<slug>/projects/                 create           (steward)
    GET    /api/v1/projects/orgs/<slug>/projects/<id>/            retrieve
    PATCH  /api/v1/projects/orgs/<slug>/projects/<id>/            update           (steward)
    GET    /api/v1/projects/orgs/<slug>/projects/<id>/summary/    money picture
    PUT    /api/v1/projects/orgs/<slug>/projects/<id>/deal/       set deal+splits  (steward)
    POST   /api/v1/projects/orgs/<slug>/projects/<id>/payouts/    record a payout  (steward)
    POST   /api/v1/projects/orgs/<slug>/projects/<id>/links/      add a link       (steward)
    DELETE /api/v1/projects/orgs/<slug>/projects/<id>/links/<lid>/ remove a link   (steward)

Every queryset is scoped to request.org; write actions are gated to steward/admin.
Reads are member-level: IsStewardOrAdmin passes SAFE_METHODS through for any member
(OrgContextMiddleware already 403s non-members), so GETs — including portfolio — are
readable by every org member. Verified for PLAN-cohort-dash.md item 4; no loosening
was needed.
"""

from django.db import transaction
from django.urls import path
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter
from rest_framework.views import APIView

from apps.drops.api import IsStewardOrAdmin, OrgScopedMixin

from . import services
from .models import Deal, Project, ProjectLink, Split
from .serializers import (
    DealSerializer,
    PayoutSerializer,
    ProjectLinkSerializer,
    ProjectSerializer,
)


class ProjectViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    serializer_class = ProjectSerializer
    permission_classes = [IsStewardOrAdmin]

    def get_queryset(self):
        return (
            Project.objects.for_org(self.request.org)
            .select_related("lead__user", "deal")
            .prefetch_related("links")
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["org"] = self.request.org
        return context

    def perform_create(self, serializer):
        serializer.save(org=self.request.org)

    @action(detail=True, methods=["get"])
    def summary(self, request, org_slug=None, pk=None):
        """The question the tracker exists to answer: budget, paid, promised, remaining."""
        return Response(services.project_summary(self.get_object()))

    @action(detail=True, methods=["put"])
    def deal(self, request, org_slug=None, pk=None):
        """Set (or replace) the project's deal and promised splits atomically."""
        project = self.get_object()
        serializer = DealSerializer(data=request.data, context={"org": request.org})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        splits = data.pop("splits", [])
        with transaction.atomic():
            Deal.objects.filter(project=project).delete()
            deal = Deal.objects.create(org=request.org, project=project, **data)
            Split.objects.bulk_create(
                Split(org=request.org, deal=deal, **split) for split in splits
            )
        project.refresh_from_db()
        return Response(services.project_summary(project), status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def payouts(self, request, org_slug=None, pk=None):
        """Record money actually paid out to a member against this project."""
        project = self.get_object()
        serializer = PayoutSerializer(data=request.data, context={"org": request.org})
        serializer.is_valid(raise_exception=True)
        serializer.save(org=request.org, project=project, created_by=request.user)
        return Response(services.project_summary(project), status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def links(self, request, org_slug=None, pk=None):
        """Add a pointer to one of the project's pieces."""
        project = self.get_object()
        serializer = ProjectLinkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(org=request.org, project=project)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @links.mapping.delete
    def remove_link(self, request, org_slug=None, pk=None):
        project = self.get_object()
        label = request.data.get("label")
        if not label:
            return Response({"label": ["This field is required."]}, status=400)
        deleted, _ = ProjectLink.objects.filter(project=project, label=label).delete()
        if not deleted:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PortfolioView(APIView):
    """The whole-org money picture for the dash's Money card (member-readable)."""

    def get(self, request, org_slug):
        return Response(services.portfolio_summary(request.org))


ORG = r"orgs/(?P<org_slug>[-\w]+)"

router = DefaultRouter()
router.register(rf"{ORG}/projects", ProjectViewSet, basename="project")

urlpatterns = router.urls + [
    path("orgs/<slug:org_slug>/portfolio/", PortfolioView.as_view(), name="project-portfolio"),
]
