"""
Committee sortition — the Committee tab (server-rendered, HTMX for verify).

Configure seats + weight window, run a seeded work-weighted draw (steward/admin), then
show the drawn committee with a "verify" affordance that re-runs the stored seed over the
stored weights and confirms the same seats come out. History of past draws below.

Every action also has a DRF endpoint in api.py; both call the shared services.
"""

from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.orgs.models import MembershipRole, WeightWindow

from . import services
from .models import SortitionDraw

STEWARD_ROLES = {MembershipRole.STEWARD, MembershipRole.ADMIN}


def _is_steward(request):
    if request.user.is_authenticated and request.user.is_superuser:
        return True
    membership = getattr(request, "membership", None)
    return membership is not None and membership.role in STEWARD_ROLES


def steward_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not _is_steward(request):
            raise PermissionDenied("Steward or admin role required for this action.")
        return view(request, *args, **kwargs)

    return wrapped


def _draw_context(request, org_slug, draw):
    return {
        "org_slug": org_slug,
        "draw": draw,
        "seats": services.selected_seats(draw),
        "unit_name": request.org.unit_name,
        "verified": services.verify_draw(draw),
    }


@login_required
def index(request, org_slug):
    """Configure + run a draw (stewards), latest result, and draw history."""
    draws = list(SortitionDraw.objects.for_org(request.org).order_by("-created_at"))
    latest = draws[0] if draws else None
    context = {
        "page_title": "Committee",
        "org_slug": org_slug,
        "can_steward": _is_steward(request),
        "windows": WeightWindow.choices,
        "default_window": request.org.valuation_config.weight_window,
        "draws": draws,
        "latest": None,
    }
    if latest is not None:
        context["latest"] = _draw_context(request, org_slug, latest)
    return render(request, "sortition/index.html", context)


@login_required
@steward_required
@require_POST
def run(request, org_slug):
    """Run a seeded work-weighted draw, then show it on the Committee page."""
    try:
        seats = int(request.POST.get("seats", ""))
    except (TypeError, ValueError):
        messages.warning(request, "Seats must be a whole number.")
        return redirect(reverse("sortition:index", kwargs={"org_slug": org_slug}))
    window = request.POST.get("window") or request.org.valuation_config.weight_window
    if window not in dict(WeightWindow.choices):
        window = request.org.valuation_config.weight_window
    seed = request.POST.get("seed", "")
    try:
        draw = services.run_draw(request.org, seats, window, seed)
    except services.SortitionError as exc:
        messages.warning(request, str(exc))
        return redirect(reverse("sortition:index", kwargs={"org_slug": org_slug}))
    messages.success(request, f"Drew {len(draw.result.get('selected', []))} seat(s).")
    return redirect(reverse("sortition:detail", kwargs={"org_slug": org_slug, "draw_id": draw.pk}))


@login_required
def detail(request, org_slug, draw_id):
    """A single draw with its selected committee and a verify affordance."""
    draw = get_object_or_404(SortitionDraw.objects.for_org(request.org), pk=draw_id)
    context = _draw_context(request, org_slug, draw)
    context.update({"page_title": f"Draw #{draw.pk}", "can_steward": _is_steward(request)})
    return render(request, "sortition/detail.html", context)


@login_required
def verify(request, org_slug, draw_id):
    """Re-run the stored seed over the stored weights; return the verify panel (HTMX)."""
    draw = get_object_or_404(SortitionDraw.objects.for_org(request.org), pk=draw_id)
    reproduced = services.reproduce(draw)
    stored = (draw.result or {}).get("selected", [])
    return render(
        request,
        "sortition/_verify_panel.html",
        {
            "org_slug": org_slug,
            "draw": draw,
            "verified": reproduced == stored,
            "reproduced": reproduced,
            "stored": stored,
        },
    )
