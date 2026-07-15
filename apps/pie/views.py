"""
Pie views — the org pie page (live shares + traceability drill-down) and the personal
standing page. Both read the computed pie from apps.pie.services; neither owns any model.

request.org / request.membership are populated by OrgContextMiddleware (every route here
is under /o/<org_slug>/), which also enforces membership.
"""

import math

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render

from .services import compute_personal_standing, compute_pie

# Categorical identity is the six-leaf palette (pattern 6 · SIX LEAVES), defined once in
# static/govkit.css as .gk-cat-0..5 with a validated set per color scheme. Views emit
# only the class index; beyond six members the cycle repeats and the label carries
# identity (color is never the only signal).
N_LEAF_COLORS = 6

# Pie geometry in the 0–100 viewBox. Wedges start at 12 o'clock and run clockwise, the
# order of the ledger below.
PIE_CX = 50.0
PIE_CY = 50.0
PIE_R = 48.0


def _cat_for(index):
    return index % N_LEAF_COLORS


def _pie_point(angle_deg):
    rad = math.radians(angle_deg - 90.0)  # 0° = 12 o'clock
    return (PIE_CX + PIE_R * math.cos(rad), PIE_CY + PIE_R * math.sin(rad))


def _svg_segments(pie):
    """
    Turn the pie into circular wedges (SVG path data + leaf-class index), one per
    member with a positive share. A lone ~100% slice gets is_full_circle instead of a
    path (a wedge whose two edges coincide would not render).
    """
    segments = []
    angle = 0.0
    for i, s in enumerate(pie.slices):
        if s.share_pct <= 0:
            continue
        sweep = float(s.share_pct) * 3.6
        seg = {
            "label": s.member_label,
            "cat": _cat_for(i),
            "share_pct": s.share_pct,
            "is_full_circle": sweep >= 359.99,
            "path": "",
        }
        if not seg["is_full_circle"]:
            x1, y1 = _pie_point(angle)
            x2, y2 = _pie_point(angle + sweep)
            large = 1 if sweep > 180.0 else 0
            seg["path"] = (
                f"M {PIE_CX:.3f} {PIE_CY:.3f} L {x1:.3f} {y1:.3f} "
                f"A {PIE_R:.3f} {PIE_R:.3f} 0 {large} 1 {x2:.3f} {y2:.3f} Z"
            )
        segments.append(seg)
        angle += sweep
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
