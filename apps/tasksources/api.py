"""
DRF endpoints for tasksources (API-first: every UI action has an endpoint).

Path-based org scoping — the SAME convention as drops/pie/exports. Every route nests an
``orgs/<org_slug>/`` segment, so OrgContextMiddleware (which keys on the ``org_slug`` view
kwarg) resolves ``request.org`` / ``request.membership`` and enforces membership (404 for
an unknown org, 403 for an authenticated non-member) exactly as it does for the HTML
pages. No ``?org=`` query param and no manual membership check here.

Endpoints:
  GET  /api/v1/tasksources/orgs/<org_slug>/tasks/                 tracked tasks for the org
  GET  /api/v1/tasksources/orgs/<org_slug>/tasks/missing_value/   the missing-value queue
  GET  /api/v1/tasksources/orgs/<org_slug>/tasks/open/            live open work (proxied)
  POST /api/v1/tasksources/orgs/<org_slug>/sync/                  run a sync (steward/admin)
"""

import urllib.error

from django.conf import settings
from django.core.cache import cache
from django.urls import path
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.orgs.models import MembershipRole

from .adapters import get_adapter
from .models import TaskSourceConfig, TrackedTask
from .services import missing_value_tasks, sync_org

_STEWARD_ROLES = {MembershipRole.ADMIN, MembershipRole.STEWARD}


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
    """Read tracked tasks + the missing-value queue. Scoped to ``request.org``."""

    serializer_class = TrackedTaskSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return TrackedTask.objects.for_org(self.request.org).select_related(
            "assignee", "assignee__user", "source"
        )

    @action(detail=False, methods=["get"])
    def missing_value(self, request, org_slug=None):
        page = self.paginate_queryset(missing_value_tasks(request.org))
        if page is not None:
            return self.get_paginated_response(self.get_serializer(page, many=True).data)
        tasks = missing_value_tasks(request.org)
        return Response(self.get_serializer(tasks, many=True).data)


class OpenTasksView(APIView):
    """Live open work for the org, proxied through its tracker adapter(s).

    PLAN-cohort-dash.md item 3: read-only, member-gated (OrgContextMiddleware),
    cached server-side for settings.GOVKIT_OPEN_TASKS_CACHE_SECONDS. Entirely
    separate from the TrackedTask valuation pipeline — nothing is persisted.
    A tracker outage returns 502 so callers can distinguish "no work" from "no answer".
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, org_slug=None):
        cache_key = f"tasksources:open_tasks:{request.org.pk}"
        payload = cache.get(cache_key)
        if payload is None:
            tasks = []
            try:
                for source in TaskSourceConfig.objects.for_org(request.org):
                    tasks.extend(get_adapter(source).fetch_open_tasks())
            except NotImplementedError:
                pass  # adapter type without open-task support: report what we have
            except (urllib.error.URLError, OSError, ValueError) as exc:
                return Response(
                    {"detail": f"Task tracker unavailable: {exc}"},
                    status=status.HTTP_502_BAD_GATEWAY,
                )
            payload = {
                "tasks": [
                    {
                        "external_id": t.external_id,
                        "ref": t.ref,
                        "subject": t.subject,
                        "assignee_label": t.assignee_label,
                        "status": t.status,
                        "external_url": t.external_url,
                        "project_slug": t.project_slug,
                    }
                    for t in tasks
                ],
                "fetched_at": timezone.now().isoformat(),
            }
            cache.set(cache_key, payload, settings.GOVKIT_OPEN_TASKS_CACHE_SECONDS)
        return Response(payload)


class SyncView(APIView):
    """Trigger a sync of every task source for the org (steward/admin only)."""

    permission_classes = [IsAuthenticated]

    def post(self, request, org_slug=None):
        membership = getattr(request, "membership", None)
        if membership is not None and membership.role not in _STEWARD_ROLES:
            raise PermissionDenied("Only stewards or admins may sync task sources.")
        results = sync_org(request.org)
        return Response(
            {
                "org": request.org.slug,
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


urlpatterns = [
    path(
        "orgs/<slug:org_slug>/tasks/",
        TrackedTaskViewSet.as_view({"get": "list"}),
        name="trackedtask-list",
    ),
    path(
        "orgs/<slug:org_slug>/tasks/missing_value/",
        TrackedTaskViewSet.as_view({"get": "missing_value"}),
        name="trackedtask-missing-value",
    ),
    path(
        "orgs/<slug:org_slug>/tasks/open/",
        OpenTasksView.as_view(),
        name="trackedtask-open",
    ),
    path(
        "orgs/<slug:org_slug>/sync/",
        SyncView.as_view(),
        name="tasksource-sync",
    ),
]
