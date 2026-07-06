"""
Pie computation — the single place that turns the earnings record into shares.

Definition (settled):

    Pie = Σ issued DropLines + OpeningBalances, per membership, per org.

Traceability is the product: every slice this module returns carries the exact
DropLines / tasks / OpeningBalances that produced it, so the UI (and the API) can drill
a share all the way back to the work that earned it — something a spreadsheet can't do.

All arithmetic is Decimal-precise. The empty-org case (zero total) yields zero shares
rather than dividing by zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional

from apps.drops.models import DropLine, DropRunState
from apps.orgs.models import Membership, OpeningBalance

ZERO = Decimal("0")
# Full-precision fraction (0..1), quantized only for a stable, comparable value.
# 8 dp keeps rounding drift negligible when shares are summed back to 1.
SHARE_Q = Decimal("0.00000001")
# Percentage for display (0..100).
PCT_Q = Decimal("0.01")
# Monetary totals mirror the model's 2-decimal-place values.
CENTS = Decimal("0.01")


def _cents(value: Decimal) -> Decimal:
    """Quantize a monetary aggregate to 2dp so 0 reads as 0.00, matching model fields."""
    return value.quantize(CENTS)


# --------------------------------------------------------------------------- #
# Provenance leaves — the "trace back to the work" payload.
# --------------------------------------------------------------------------- #
@dataclass
class TaskProvenance:
    """A single tracked task that fed a drop line."""

    task_id: int
    external_id: str
    external_url: str
    subject: str


@dataclass
class LineProvenance:
    """One drop line and the tasks behind it."""

    line_id: int
    run_id: int
    computed_value: Decimal
    adjustment: Decimal
    adjustment_reason: str
    final_value: Decimal
    tasks: List[TaskProvenance] = field(default_factory=list)


@dataclass
class OpeningProvenance:
    """One imported opening balance."""

    opening_balance_id: int
    value: Decimal
    source_note: str


# --------------------------------------------------------------------------- #
# Aggregates.
# --------------------------------------------------------------------------- #
@dataclass
class PieSlice:
    """One member's stake in the org pie, fully traceable."""

    membership_id: int
    member_label: str
    role: str
    drops_total: Decimal
    opening_total: Decimal
    issued_total: Decimal
    share: Decimal  # fraction of org total, 0..1
    share_pct: Decimal  # percentage for display, 0..100
    lines: List[LineProvenance] = field(default_factory=list)
    opening_balances: List[OpeningProvenance] = field(default_factory=list)


@dataclass
class Pie:
    """The whole org pie: total issued equity + every member's traceable slice."""

    org_slug: str
    unit_name: str
    total: Decimal
    member_count: int
    slices: List[PieSlice] = field(default_factory=list)


@dataclass
class Standing:
    """One member's personal standing in an org: issued vs pending, all traceable."""

    org_slug: str
    unit_name: str
    membership_id: int
    member_label: str
    # Realized: issued drop lines + opening balances = the member's pie stake.
    issued_total: Decimal
    opening_total: Decimal
    realized_total: Decimal
    share: Decimal
    share_pct: Decimal
    issued_lines: List[LineProvenance] = field(default_factory=list)
    opening_balances: List[OpeningProvenance] = field(default_factory=list)
    # Pending (still-open runs) — provisional, not yet part of the pie.
    pending_total: Decimal = ZERO
    pending_lines: List[LineProvenance] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Builders.
# --------------------------------------------------------------------------- #
def _task_provenance(task) -> TaskProvenance:
    return TaskProvenance(
        task_id=task.pk,
        external_id=task.external_id,
        external_url=task.external_url,
        subject=task.subject,
    )


def _line_provenance(line: DropLine) -> LineProvenance:
    return LineProvenance(
        line_id=line.pk,
        run_id=line.run_id,
        computed_value=line.computed_value,
        adjustment=line.adjustment,
        adjustment_reason=line.adjustment_reason,
        final_value=line.final_value,
        tasks=[_task_provenance(t) for t in line.tasks.all()],
    )


def _opening_provenance(ob: OpeningBalance) -> OpeningProvenance:
    return OpeningProvenance(
        opening_balance_id=ob.pk,
        value=ob.value,
        source_note=ob.source_note,
    )


def _member_label(membership: Membership) -> str:
    """Live display label — never a committed name. The user's short name."""
    return membership.user.get_short_name()


def _lines_by_member(org, state) -> Dict[int, List[DropLine]]:
    """Drop lines for an org in the given run state, grouped by membership id."""
    lines = (
        DropLine.objects.for_org(org)
        .filter(run__state=state)
        .select_related("membership", "membership__user", "run")
        .prefetch_related("tasks")
    )
    by_member: Dict[int, List[DropLine]] = {}
    for line in lines:
        by_member.setdefault(line.membership_id, []).append(line)
    return by_member


def _openings_by_member(org) -> Dict[int, List[OpeningBalance]]:
    openings = OpeningBalance.objects.filter(org=org).select_related("membership")
    by_member: Dict[int, List[OpeningBalance]] = {}
    for ob in openings:
        by_member.setdefault(ob.membership_id, []).append(ob)
    return by_member


def compute_pie(org) -> Pie:
    """
    Compute the current pie for an org.

    Per membership: issued_total = Σ final_value of DropLines in APPROVED DropRuns
    + Σ OpeningBalance.value. Each member's share is issued_total / org total.

    Returns a :class:`Pie` whose slices are sorted by issued_total (desc, then label) and
    carry full provenance (the exact lines/tasks/opening balances) for drill-down. The
    zero-total org is handled without dividing by zero (all shares are 0).
    """
    memberships = list(Membership.objects.filter(org=org).select_related("user").order_by("id"))
    issued_by_member = _lines_by_member(org, DropRunState.APPROVED)
    openings_by_member = _openings_by_member(org)

    slices: List[PieSlice] = []
    total = ZERO
    for m in memberships:
        lines = issued_by_member.get(m.id, [])
        openings = openings_by_member.get(m.id, [])
        drops_total = _cents(sum((ln.final_value for ln in lines), ZERO))
        opening_total = _cents(sum((ob.value for ob in openings), ZERO))
        issued_total = drops_total + opening_total
        total += issued_total
        slices.append(
            PieSlice(
                membership_id=m.id,
                member_label=_member_label(m),
                role=m.role,
                drops_total=drops_total,
                opening_total=opening_total,
                issued_total=issued_total,
                share=ZERO,
                share_pct=ZERO,
                lines=[_line_provenance(ln) for ln in lines],
                opening_balances=[_opening_provenance(ob) for ob in openings],
            )
        )

    # Derive shares once the org total is known (guard the empty-org divide-by-zero).
    if total > ZERO:
        for s in slices:
            frac = s.issued_total / total
            s.share = frac.quantize(SHARE_Q)
            s.share_pct = (frac * Decimal("100")).quantize(PCT_Q)

    slices.sort(key=lambda s: (-s.issued_total, s.member_label.lower()))

    return Pie(
        org_slug=org.slug,
        unit_name=org.unit_name,
        total=total,
        member_count=len(memberships),
        slices=slices,
    )


def compute_personal_standing(org, membership: Membership) -> Standing:
    """
    Personal standing for one member in an org.

    Separates realized equity (issued drop lines + opening balances, which count toward
    the pie) from pending equity (lines in still-open runs, provisional). Both carry full
    provenance so the member can always see exactly where they stand and why.
    """
    pie = compute_pie(org)
    slice_: Optional[PieSlice] = next(
        (s for s in pie.slices if s.membership_id == membership.id), None
    )

    pending_lines = _lines_by_member(org, DropRunState.OPEN).get(membership.id, [])
    pending_total = _cents(sum((ln.final_value for ln in pending_lines), ZERO))

    if slice_ is None:
        return Standing(
            org_slug=org.slug,
            unit_name=org.unit_name,
            membership_id=membership.id,
            member_label=_member_label(membership),
            issued_total=ZERO,
            opening_total=ZERO,
            realized_total=ZERO,
            share=ZERO,
            share_pct=ZERO,
            pending_total=pending_total,
            pending_lines=[_line_provenance(ln) for ln in pending_lines],
        )

    return Standing(
        org_slug=org.slug,
        unit_name=org.unit_name,
        membership_id=membership.id,
        member_label=slice_.member_label,
        issued_total=slice_.drops_total,
        opening_total=slice_.opening_total,
        realized_total=slice_.issued_total,
        share=slice_.share,
        share_pct=slice_.share_pct,
        issued_lines=slice_.lines,
        opening_balances=slice_.opening_balances,
        pending_total=pending_total,
        pending_lines=[_line_provenance(ln) for ln in pending_lines],
    )
