"""
Pie views — the org pie page (live shares + traceability drill-down) and the personal
standing page. Both read the computed pie from apps.pie.services; neither owns any model.

request.org / request.membership are populated by OrgContextMiddleware (every route here
is under /o/<org_slug>/), which also enforces membership.
"""

import math

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.captable import services as captable
from apps.drops.models import DropRun
from apps.drops.views import _is_steward
from apps.orgs.models import MembershipRole, PiePhase

from .services import (
    LOCK_NO,
    LOCK_YES,
    LockError,
    cast_lock_ballot,
    close_lock_vote,
    compute_personal_standing,
    compute_pie,
    current_lock_vote,
    lock_progress,
    start_lock_vote,
)

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


def _wedges(items):
    """
    Turn (label, pct, leaf-class index) items into circular wedges (SVG path data), one
    per item with a positive share. A lone ~100% item gets is_full_circle instead of a
    path (a wedge whose two edges coincide would not render).
    """
    segments = []
    angle = 0.0
    for label, pct, cat in items:
        if pct <= 0:
            continue
        sweep = float(pct) * 3.6
        seg = {
            "label": label,
            "cat": cat,
            "share_pct": pct,
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


def _svg_segments(pie):
    """The governance pie as wedges: one per member or sponsor slice, in ledger order."""
    return _wedges((s.member_label, s.share_pct, _cat_for(i)) for i, s in enumerate(pie.slices))


POOL_KINDS = (captable.KIND_FOUNDER, captable.KIND_SPONSOR, captable.KIND_POOL_UNEARNED)
VIEW_GOVERNANCE = "governance"
VIEW_EQUITY = "equity"
RECENT_RUNS = 5


def _equity(org):
    """The whole company from the cap table service: founders pool lines, then the
    investor side (each investor, then "Not yet sold" or the outside line)."""
    table = captable.compute_cap_table(org)
    rows = [{"line": ln, "cat": _cat_for(i)} for i, ln in enumerate(table.lines)]
    return {
        "table": table,
        "pool_rows": [r for r in rows if r["line"].kind in POOL_KINDS],
        "investor_rows": [r for r in rows if r["line"].kind not in POOL_KINDS],
        "pool_only": table.scope == captable.Scope.POOL_ONLY,
        "segments": _wedges((r["line"].label, r["line"].pct_display, r["cat"]) for r in rows),
    }


def _rows_with_cat(pie):
    """Pair each slice with its stable leaf class so the table swatches match the bar."""
    return [{"slice": s, "cat": _cat_for(i)} for i, s in enumerate(pie.slices)]


def _is_pie_admin(request) -> bool:
    membership = request.membership
    return request.user.is_superuser or (
        membership is not None and membership.role == MembershipRole.ADMIN
    )


@login_required
def index(request, org_slug):
    """Org pie: who holds what share of the org's issued equity, every slice traceable.

    The page also carries the pie's phase: the setup card while the starting split is
    being recorded, the lock-in vote while the pie is launched, and the lock-in date
    once the split is the record.
    """
    org = request.org
    pie = compute_pie(org)
    membership = request.membership
    view = VIEW_EQUITY if request.GET.get("view") == VIEW_EQUITY else VIEW_GOVERNANCE

    lock = current_lock_vote(org) if org.pie_phase == PiePhase.LAUNCHED else None
    progress = lock_progress(lock.vote) if lock else None
    my_choice = None
    if lock and membership:
        ballot = lock.vote.ballots.filter(membership=membership).first()
        my_choice = ballot.choice if ballot else None

    cohort = org.cohort
    cohort_ended = bool(cohort and cohort.ends_on and cohort.ends_on <= timezone.localdate())

    context = {
        "page_title": "Pie",
        "org_slug": org_slug,
        "pie": pie,
        "rows": _rows_with_cat(pie),
        "view": view,
        "segments": _svg_segments(pie) if view == VIEW_GOVERNANCE else [],
        "equity": _equity(org) if view == VIEW_EQUITY else None,
        # Drop history under the pie; the run itself is the Drops page's open_run.
        "runs": DropRun.objects.for_org(org).order_by("-opened_at")[:RECENT_RUNS],
        "can_steward": _is_steward(request),
        "pie_phase": org.pie_phase,
        "is_pie_admin": _is_pie_admin(request),
        # The starting split is adjustable in place until the team locks it in. After
        # lock-in the control is gone AND the endpoint refuses (apps.orgs.equity), so
        # this only decides what is drawn.
        "can_set_starting": _is_pie_admin(request) and org.pie_phase != PiePhase.LOCKED,
        "is_member": membership is not None,
        # Live lock-in vote, if one is running.
        "lock": lock,
        "lock_progress": progress,
        "lock_my_choice": my_choice,
        "lock_yes": LOCK_YES,
        "lock_no": LOCK_NO,
        # Once the cohort has ended, the page prompts the team to lock in.
        "cohort_ended": cohort_ended,
        "pie_locked_at": org.pie_locked_at,
        # A team whose equity record started life elsewhere (Slicing Pie's Pie
        # Slicer) shows the pointer so starting values can be checked against it.
        "outside_pie_url": org.pie_url,
        "outside_pie_as_of": org.pie_as_of,
    }
    return render(request, "pie/index.html", context)


@login_required
def lock_start(request, org_slug):
    """Admin opens the lock-in vote (POST only)."""
    if request.method != "POST":
        return redirect("pie:index", org_slug=org_slug)
    if not _is_pie_admin(request):
        messages.error(request, "Only an admin can start the lock-in vote.")
        return redirect("pie:index", org_slug=org_slug)
    try:
        start_lock_vote(request.org)
        messages.success(
            request,
            "Lock-in vote open. A majority of the members' stake makes this split "
            "the record. Money doesn't vote; it gets preferred shares in the SAFE.",
        )
    except LockError as exc:
        messages.error(request, str(exc))
    return redirect("pie:index", org_slug=org_slug)


@login_required
def lock_cast(request, org_slug):
    """A member casts (or changes) their lock-in ballot (POST only)."""
    if request.method != "POST":
        return redirect("pie:index", org_slug=org_slug)
    lock = current_lock_vote(request.org)
    if lock is None:
        messages.error(request, "There is no lock-in vote running.")
        return redirect("pie:index", org_slug=org_slug)
    if request.membership is None:
        messages.error(request, "Only members vote on the lock-in.")
        return redirect("pie:index", org_slug=org_slug)
    choice = request.POST.get("choice", "")
    try:
        lock = cast_lock_ballot(lock, request.membership, choice)
    except Exception as exc:
        messages.error(request, str(exc))
        return redirect("pie:index", org_slug=org_slug)
    if lock.locked:
        messages.success(
            request,
            "That carried it. Locked in by majority decision — this split is the record.",
        )
    else:
        messages.success(request, "Ballot recorded.")
    return redirect("pie:index", org_slug=org_slug)


@login_required
def lock_close(request, org_slug):
    """Admin closes the lock-in vote early (POST only).

    If a majority is already there it locks; otherwise the pie stays launched and
    adjustable, and the team can start another vote whenever they are ready.
    """
    if request.method != "POST":
        return redirect("pie:index", org_slug=org_slug)
    if not _is_pie_admin(request):
        messages.error(request, "Only an admin can close the lock-in vote.")
        return redirect("pie:index", org_slug=org_slug)
    lock = current_lock_vote(request.org)
    if lock is None:
        messages.error(request, "There is no lock-in vote running.")
        return redirect("pie:index", org_slug=org_slug)
    lock = close_lock_vote(lock)
    if lock.locked:
        messages.success(request, "Locked in by majority decision — this split is the record.")
    else:
        messages.info(
            request,
            "Vote closed without a majority. The starting split stays adjustable — "
            "start another vote when the team is ready.",
        )
    return redirect("pie:index", org_slug=org_slug)


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
