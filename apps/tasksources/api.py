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
  GET  /api/v1/tasksources/orgs/<org_slug>/tasks/<external_id>/   one task, with its body
  POST /api/v1/tasksources/orgs/<org_slug>/tasks/<external_id>/   edit that task
  POST /api/v1/tasksources/orgs/<org_slug>/sync/                  run a sync (steward/admin)

Route order matters: ``tasks/missing_value/`` and ``tasks/open/`` are declared before
the ``<external_id>`` route so those words are never read as a task id.
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

from apps.orgs.embed_auth import EmbedSessionAuthentication
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


class TaskDetailView(APIView):
    """One task, read and edited where it lives (the dash opens it in place).

    The org's OWN task sources are the only ones tried, so the credentials bound to
    this org are what reach the tracker: passing another team's story id finds
    nothing rather than reaching across. Any member may edit — the board is the
    team's, and the tracker stays the authority on what a task says.

    Nothing is persisted here. The tracker is the record; TrackedTask remains the
    valuation mirror and is refreshed by sync, not by an edit.
    """

    permission_classes = [IsAuthenticated]
    authentication_classes = [EmbedSessionAuthentication]

    def _sources(self, request):
        return list(TaskSourceConfig.objects.for_org(request.org))

    def _payload(self, detail, source):
        return {
            "external_id": detail.external_id,
            "subject": detail.subject,
            "description": detail.description,
            "status": detail.status,
            "external_url": detail.external_url,
            "assignee_label": detail.assignee_label,
            "ref": detail.ref,
            "project_slug": detail.project_slug,
            "version": detail.version,
            "is_closed": detail.is_closed,
            "source": source.pk,
        }

    def _find(self, request, external_id):
        """Return (adapter, source, detail) for the first source that has this task."""
        unsupported = False
        for source in self._sources(request):
            adapter = get_adapter(source)
            try:
                return adapter, source, adapter.fetch_task(external_id)
            except LookupError:
                continue
            except NotImplementedError:
                unsupported = True
                continue
        if unsupported:
            raise NotImplementedError
        raise LookupError

    def get(self, request, org_slug=None, external_id=None):
        try:
            _adapter, source, detail = self._find(request, external_id)
        except LookupError:
            return Response({"detail": "No such task."}, status=status.HTTP_404_NOT_FOUND)
        except NotImplementedError:
            return Response(
                {"detail": "This tracker cannot open a single task."},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return Response(
                {"detail": f"Task tracker unavailable: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(self._payload(detail, source))

    def post(self, request, org_slug=None, external_id=None):
        subject = request.data.get("subject")
        description = request.data.get("description")
        if subject is None and description is None:
            return Response(
                {"detail": "Nothing to change."}, status=status.HTTP_400_BAD_REQUEST
            )
        if subject is not None and not str(subject).strip():
            return Response(
                {"detail": "A task needs a title."}, status=status.HTTP_400_BAD_REQUEST
            )
        try:
            adapter, source, detail = self._find(request, external_id)
            updated = adapter.update_task(
                external_id,
                subject=None if subject is None else str(subject).strip(),
                description=None if description is None else str(description),
                version=detail.version,
            )
        except LookupError:
            return Response({"detail": "No such task."}, status=status.HTTP_404_NOT_FOUND)
        except NotImplementedError:
            return Response(
                {"detail": "This tracker is read-only."},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return Response(
                {"detail": f"Task tracker unavailable: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        # The open-tasks card is cached; an edit the person just made must not be
        # missing from the next paint.
        cache.delete(f"tasksources:open_tasks:{request.org.pk}")
        return Response(self._payload(updated, source))


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
        "orgs/<slug:org_slug>/tasks/<str:external_id>/",
        TaskDetailView.as_view(),
        name="trackedtask-detail-live",
    ),
    path(
        "orgs/<slug:org_slug>/sync/",
        SyncView.as_view(),
        name="tasksource-sync",
    ),
]
