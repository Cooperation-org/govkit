"""
Pie views — the org pie page (live shares + traceability drill-down) and the personal
standing page. Both read the computed pie from apps.pie.services; neither owns any model.

request.org / request.membership are populated by OrgContextMiddleware (every route here
is under /o/<org_slug>/), which also enforces membership.
"""

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render

from .services import compute_personal_standing, compute_pie

# Accessible, offline (no CDN) categorical palette for slice colours. Cycled by index.
SLICE_COLORS = [
    "#3366cc",
    "#dc3912",
    "#ff9900",
    "#109618",
    "#990099",
    "#0099c6",
    "#dd4477",
    "#66aa00",
    "#b82e2e",
    "#316395",
]


def _color_for(index):
    return SLICE_COLORS[index % len(SLICE_COLORS)]


def _svg_segments(pie):
    """
    Turn the pie into a list of stacked-bar segments (percent offset + width + colour).

    Widths use share_pct directly, so segments line up with the numbers shown in the
    table. Members with a zero share are dropped from the bar (nothing to draw).
    """
    segments = []
    offset = Decimal("0")
    for i, s in enumerate(pie.slices):
        if s.share_pct <= 0:
            continue
        segments.append(
            {
                "label": s.member_label,
                "x": offset,
                "width": s.share_pct,
                "color": _color_for(i),
                "share_pct": s.share_pct,
            }
        )
        offset += s.share_pct
    return segments


def _rows_with_color(pie):
    """Pair each slice with its stable colour so the table swatches match the bar."""
    return [{"slice": s, "color": _color_for(i)} for i, s in enumerate(pie.slices)]


@login_required
def index(request, org_slug):
    """Org pie: who holds what share of the org's issued equity, every slice traceable."""
    pie = compute_pie(request.org)
    context = {
        "page_title": "Pie",
        "org_slug": org_slug,
        "pie": pie,
        "rows": _rows_with_color(pie),
        "segments": _svg_segments(pie),
    }
    return render(request, "pie/index.html", context)


@login_required
def standing(request, org_slug):
    """The logged-in member's personal standing: pending vs issued, with provenance."""
    membership = request.membership
    if membership is None:
        # Superusers inspect via the org pie page; personal standing needs a membership.
        raise Http404("No membership in this org to show personal standing for.")
    standing_data = compute_personal_standing(request.org, membership)
    context = {
        "page_title": "My standing",
        "org_slug": org_slug,
        "standing": standing_data,
    }
    return render(request, "pie/standing.html", context)
