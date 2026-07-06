"""
tasksources HTML views: the task-source list + the missing-value review queue, plus a
POST to run a sync. Every action here has a matching DRF endpoint in api.py (API-first).

Org context (``request.org`` / ``request.membership``) is set by OrgContextMiddleware for
these ``/o/<org_slug>/tasks/`` routes.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.orgs.models import MembershipRole

from .models import TaskSourceConfig
from .services import missing_value_tasks, sync_org

_STEWARD_ROLES = {MembershipRole.ADMIN, MembershipRole.STEWARD}


def _is_steward(request):
    if request.user.is_superuser:
        return True
    return request.membership is not None and request.membership.role in _STEWARD_ROLES


@login_required
def index(request, org_slug):
    """Task sources + the missing-value queue for the org."""
    org = request.org
    context = {
        "page_title": "Task sources",
        "org_slug": org_slug,
        "sources": TaskSourceConfig.objects.for_org(org),
        "missing_tasks": missing_value_tasks(org),
        "can_sync": _is_steward(request),
    }
    return render(request, "tasksources/index.html", context)


@login_required
@require_POST
def sync_now(request, org_slug):
    """Run a sync for every source in the org (steward/admin only)."""
    if not _is_steward(request):
        messages.error(request, "Only stewards or admins may sync task sources.")
        return redirect("tasksources:index", org_slug=org_slug)

    results = sync_org(request.org)
    if not results:
        messages.warning(request, "No task sources are configured for this org yet.")
    else:
        synced = sum(r.synced for r in results)
        unassigned = sum(r.unassigned for r in results)
        messages.success(
            request,
            f"Synced {synced} task(s) from {len(results)} source(s); "
            f"{unassigned} had no mapped assignee.",
        )
    return redirect("tasksources:index", org_slug=org_slug)
