"""
Drops — steward flow views (server-rendered, HTMX for inline adjust).

Flow: index (run list) -> open a run -> review (tasks-missing-value queue + per-member
totals) -> per-line adjust with a required reason -> approve -> issued (immutable).

Every action here also has a DRF endpoint in api.py (API-first); both call the shared
services in services.py so the logic lives in exactly one place.
"""

from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.orgs.models import MembershipRole

from . import services
from .forms import AdjustLineForm
from .models import DropLine, DropRun

STEWARD_ROLES = {MembershipRole.STEWARD, MembershipRole.ADMIN}


def _is_steward(request):
    """Superusers pass (inspection); otherwise the membership must be steward/admin."""
    if request.user.is_authenticated and request.user.is_superuser:
        return True
    membership = getattr(request, "membership", None)
    return membership is not None and membership.role in STEWARD_ROLES


def steward_required(view):
    """Gate a mutating view to steward/admin (or superuser)."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not _is_steward(request):
            raise PermissionDenied("Steward or admin role required for this action.")
        return view(request, *args, **kwargs)

    return wrapped


@login_required
def index(request, org_slug):
    """Run list for the org, newest first, with an 'open a run' control for stewards."""
    runs = DropRun.objects.for_org(request.org).order_by("-opened_at")
    return render(
        request,
        "drops/index.html",
        {
            "page_title": "Drops",
            "org_slug": org_slug,
            "runs": runs,
            "can_steward": _is_steward(request),
        },
    )


@login_required
@steward_required
@require_POST
def open_run(request, org_slug):
    """Open a new run over the org's eligible done tasks, then go to its review page."""
    try:
        run = services.open_run(
            request.org,
            opened_by_membership=request.membership,
            opened_by_user=request.user,
        )
    except services.NoEligibleTasks as exc:
        messages.warning(request, str(exc))
        return redirect(reverse("drops:index", kwargs={"org_slug": org_slug}))
    messages.success(request, f"Opened run #{run.pk} with {run.lines.count()} line(s).")
    return redirect(reverse("drops:review", kwargs={"org_slug": org_slug, "run_id": run.pk}))


@login_required
def review(request, org_slug, run_id):
    """Review page: tasks-missing-value queue + per-member line totals for a run."""
    run = get_object_or_404(DropRun.objects.for_org(request.org), pk=run_id)
    payload = services.review_queue(run)
    payload.update(
        {
            "page_title": f"Review run #{run.pk}",
            "org_slug": org_slug,
            "can_steward": _is_steward(request),
        }
    )
    return render(request, "drops/review.html", payload)


@login_required
@steward_required
@require_POST
def adjust_line(request, org_slug, line_id):
    """Apply an adjustment to a line (HTMX). Returns the updated line row partial."""
    line = get_object_or_404(
        DropLine.objects.for_org(request.org).select_related("run", "membership__user"),
        pk=line_id,
    )
    if line.run.is_approved:
        raise PermissionDenied("Cannot adjust a line after its run is approved.")

    form = AdjustLineForm(request.POST)
    if form.is_valid():
        try:
            services.adjust_line(
                line,
                form.cleaned_data["adjustment"],
                form.cleaned_data["adjustment_reason"],
                adjusted_by=request.membership,
            )
        except ValidationError as exc:
            form.add_error(None, exc.messages)

    line.refresh_from_db()
    return render(
        request,
        "drops/_line_row.html",
        {"line": line, "org_slug": org_slug, "can_steward": True, "form": form},
    )


@login_required
@steward_required
@require_POST
def approve_run(request, org_slug, run_id):
    """Approve a run: lines become issued + immutable. Back to the review page."""
    run = get_object_or_404(DropRun.objects.for_org(request.org), pk=run_id)
    try:
        services.approve_run(run, approved_by_membership=request.membership)
    except ValueError as exc:
        messages.warning(request, str(exc))
    else:
        messages.success(request, f"Run #{run.pk} approved — lines are now issued.")
    return redirect(reverse("drops:review", kwargs={"org_slug": org_slug, "run_id": run.pk}))
