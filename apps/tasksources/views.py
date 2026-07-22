"""
tasksources HTML views: the task-source list + the missing-value review queue, plus a
POST to run a sync. Every action here has a matching DRF endpoint in api.py (API-first).

Org context (``request.org`` / ``request.membership``) is set by OrgContextMiddleware for
these ``/o/<org_slug>/tasks/`` routes.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.orgs.models import MembershipRole

from .forms import TaskSourceConnectForm
from .models import TaskSourceConfig
from .services import missing_value_tasks, sync_org

_STEWARD_ROLES = {MembershipRole.ADMIN, MembershipRole.STEWARD}


def _is_steward(request):
    if request.user.is_superuser:
        return True
    return request.membership is not None and request.membership.role in _STEWARD_ROLES


def _is_admin(request):
    if request.user.is_superuser:
        return True
    return request.membership is not None and request.membership.role == MembershipRole.ADMIN


def _source_or_404(org, source_id):
    """Resolve a source pk STRICTLY within the org (cross-org pks 404)."""
    if not str(source_id).isdigit():
        raise Http404("No such task source.")
    return get_object_or_404(TaskSourceConfig.objects.for_org(org), pk=source_id)


def _index_context(request, org_slug, connect_form=None, editing_source=None):
    org = request.org
    return {
        "page_title": "Task sources",
        "org_slug": org_slug,
        "sources": TaskSourceConfig.objects.for_org(org),
        "missing_tasks": missing_value_tasks(org),
        "can_sync": _is_steward(request),
        "can_manage": _is_admin(request),
        "connect_form": connect_form,
        "editing_source": editing_source,
    }


@login_required
def index(request, org_slug):
    """Task sources + the missing-value queue; org admins also get the connect form.

    ``?source=<pk>`` (admin only, org-scoped) binds the form to an existing source for
    editing; otherwise the form creates a new one.
    """
    connect_form = None
    editing_source = None
    if _is_admin(request):
        source_id = request.GET.get("source")
        if source_id:
            editing_source = _source_or_404(request.org, source_id)
        connect_form = TaskSourceConnectForm(instance=editing_source)
    return render(
        request,
        "tasksources/index.html",
        _index_context(request, org_slug, connect_form, editing_source),
    )


@login_required
@require_POST
def save_source(request, org_slug):
    """Create or update a task source config (org admin only).

    The form's api_token handling is write-only: a posted token replaces the stored one,
    a blank token keeps it, and the stored token is never rendered back.
    """
    if not _is_admin(request):
        raise PermissionDenied("Only org admins may configure task sources.")
    source_id = request.POST.get("source_id", "")
    editing_source = _source_or_404(request.org, source_id) if source_id else None
    form = TaskSourceConnectForm(request.POST, instance=editing_source)
    if form.is_valid():
        source = form.save(commit=False)
        source.org = request.org
        source.save()
        messages.success(request, "Task source saved.")
        return redirect("tasksources:index", org_slug=org_slug)
    return render(
        request,
        "tasksources/index.html",
        _index_context(request, org_slug, form, editing_source),
    )


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
