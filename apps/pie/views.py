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

# Categorical identity is the six-leaf palette (pattern 6 · SIX LEAVES), defined once in
# static/govkit.css as .gk-cat-0..5 with a validated set per color scheme. Views emit
# only the class index; beyond six members the cycle repeats and the label carries
# identity (color is never the only signal).
N_LEAF_COLORS = 6

# Visual-only gap between adjacent bar segments (in share-percent units of the 0–100
# viewBox), per the mark spec: fills never touch. Applied to the drawn width only, so
# offsets still line up with the real numbers in the ledger below.
SEGMENT_GAP = Decimal("0.4")


def _cat_for(index):
    return index % N_LEAF_COLORS


def _svg_segments(pie):
    """
    Turn the pie into a list of stacked-bar segments (percent offset + drawn width +
    leaf-class index).

    Offsets use share_pct directly, so segments line up with the numbers shown in the
    table; drawn widths give up a hair of space to the gap (never below a visible
    minimum). Members with a zero share are dropped from the bar (nothing to draw).
    """
    segments = []
    offset = Decimal("0")
    for i, s in enumerate(pie.slices):
        if s.share_pct <= 0:
            continue
        drawn = max(s.share_pct - SEGMENT_GAP, Decimal("0.3"))
        segments.append(
            {
                "label": s.member_label,
                "x": offset,
                "width": drawn,
                "cat": _cat_for(i),
                "share_pct": s.share_pct,
            }
        )
        offset += s.share_pct
    return segments


def _rows_with_cat(pie):
    """Pair each slice with its stable leaf class so the table swatches match the bar."""
    return [{"slice": s, "cat": _cat_for(i)} for i, s in enumerate(pie.slices)]


@login_required
def index(request, org_slug):
    """Org pie: who holds what share of the org's issued equity, every slice traceable."""
    pie = compute_pie(request.org)
    context = {
        "page_title": "Pie",
        "org_slug": org_slug,
        "pie": pie,
        "rows": _rows_with_cat(pie),
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
