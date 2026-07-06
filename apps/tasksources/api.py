"""
DRF endpoints for tasksources (API-first: every UI action has an endpoint).

The router mounts at ``/api/v1/tasksources/`` — outside the ``/o/<org_slug>/`` prefix, so
OrgContextMiddleware does not populate ``request.org`` here. Every endpoint therefore takes
an explicit ``?org=<slug>`` (or ``org`` in the body) and verifies the caller's Membership,
scoping every queryset to that org.

Endpoints:
  GET  /api/v1/tasksources/tasks/?org=<slug>                 tracked tasks for the org
  GET  /api/v1/tasksources/tasks/missing_value/?org=<slug>   the missing-value queue
  POST /api/v1/tasksources/tasks/sync/  {"org": "<slug>"}     run a sync (steward/admin)
"""

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from apps.orgs.models import Membership, MembershipRole, Org

from .models import TrackedTask
from .services import missing_value_tasks, sync_org

_STEWARD_ROLES = {MembershipRole.ADMIN, MembershipRole.STEWARD}


def _resolve_org(request):
    """Resolve the target org from ``org`` (query or body) and enforce membership.

    Returns ``(org, membership)``. Superusers pass with ``membership=None``. Raises the
    DRF exception matching the middleware's HTML behavior (404 unknown / 403 non-member).
    """
    slug = request.query_params.get("org") or request.data.get("org")
    if not slug:
        raise ValidationError({"org": "This query parameter is required."})
    try:
        org = Org.objects.get(slug=slug)
    except Org.DoesNotExist as exc:
        raise NotFound(f"No org with slug '{slug}'.") from exc

    if request.user.is_superuser:
        return org, None
    membership = Membership.objects.filter(org=org, user=request.user).first()
    if membership is None:
        raise PermissionDenied("You are not a member of this organization.")
    return org, membership


class TrackedTaskSerializer(serializers.ModelSerializer):
    assignee_label = serializers.SerializerMethodField()
    is_missing_value = serializers.BooleanField(read_only=True)

    class Meta:
        model = TrackedTask
        fields = [
            "id",
            "source",
            "external_id",
            "external_url",
            "subject",
            "assignee",
            "assignee_label",
            "claimed_value",
            "hours",
            "cash",
            "status",
            "fetched_at",
            "is_missing_value",
        ]

    def get_assignee_label(self, obj):
        # Avoid leaking names: use the stable Taiga identity, not a person's display name.
        if obj.assignee is None:
            return None
        return obj.assignee.taiga_username or str(obj.assignee.taiga_user_id or "")


class TrackedTaskViewSet(viewsets.ReadOnlyModelViewSet):
    """Read tracked tasks + trigger a sync. Always scoped to ``?org=<slug>``."""

    serializer_class = TrackedTaskSerializer

    def get_queryset(self):
        org, _ = _resolve_org(self.request)
        return TrackedTask.objects.for_org(org).select_related(
            "assignee", "assignee__user", "source"
        )

    @action(detail=False, methods=["get"])
    def missing_value(self, request):
        org, _ = _resolve_org(request)
        page = self.paginate_queryset(missing_value_tasks(org))
        if page is not None:
            return self.get_paginated_response(self.get_serializer(page, many=True).data)
        tasks = missing_value_tasks(org)
        return Response(self.get_serializer(tasks, many=True).data)

    @action(detail=False, methods=["post"])
    def sync(self, request):
        org, membership = _resolve_org(request)
        if membership is not None and membership.role not in _STEWARD_ROLES:
            raise PermissionDenied("Only stewards or admins may sync task sources.")
        results = sync_org(org)
        return Response(
            {
                "org": org.slug,
                "sources": [
                    {
                        "source_id": r.source_id,
                        "fetched": r.fetched,
                        "created": r.created,
                        "updated": r.updated,
                        "unassigned": r.unassigned,
                        "errors": r.errors,
                    }
                    for r in results
                ],
            },
            status=status.HTTP_200_OK,
        )


router = DefaultRouter()
router.register(r"tasks", TrackedTaskViewSet, basename="trackedtask")

urlpatterns = router.urls
