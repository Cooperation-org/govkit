"""
Pie computation — the single place that turns the earnings record into shares.

Definition (settled):

    Pie = Σ issued DropLines + OpeningBalances, per membership, per org,
          plus Σ OrgStakes, per holder org.

Almost every holder is a person, reached through their Membership. The exception is a
sponsor: an outside company that funded the venture, which holds OrgStake rows and no
membership — see apps.orgs.models.OrgStake for why that is a separate table. Both kinds
are slices of the same total and dilute together, so a slice says which it is via
``holder_kind`` rather than the caller having to know.

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

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.drops.models import DropLine, DropRunState
from apps.orgs.models import Membership, OpeningBalance, OrgStake, PiePhase

ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")
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


@dataclass
class StakeProvenance:
    """One grant of equity to a sponsoring org.

    ``value`` is always the resolved amount. ``target_pct`` is set when the stake was
    agreed as a share of the starting split, so the UI can show "10% of the start"
    next to the number it currently resolves to.
    """

    stake_id: int
    value: Decimal
    source_note: str
    target_pct: Optional[Decimal] = None


# --------------------------------------------------------------------------- #
# Aggregates.
# --------------------------------------------------------------------------- #
HOLDER_MEMBER = "member"
HOLDER_SPONSOR = "sponsor"
# What a sponsor's row shows in the Role column. Not a MembershipRole: it holds no
# membership and casts no vote.
SPONSOR_ROLE_LABEL = "sponsor"


@dataclass
class PieSlice:
    """One holder's stake in the org pie, fully traceable.

    Usually a member, reached by ``membership_id``. When ``holder_kind`` is
    :data:`HOLDER_SPONSOR` the holder is an outside company: ``membership_id`` is None,
    ``holder_slug`` names it, and the value sits in ``stakes`` rather than in ``lines``
    or ``opening_balances``. ``member_label`` is the display name either way, so
    anything that only renders a name and a share needs no branch.
    """

    membership_id: Optional[int]
    member_label: str
    role: str
    drops_total: Decimal
    opening_total: Decimal
    issued_total: Decimal
    share: Decimal  # fraction of org total, 0..1
    share_pct: Decimal  # percentage for display, 0..100
    lines: List[LineProvenance] = field(default_factory=list)
    opening_balances: List[OpeningProvenance] = field(default_factory=list)
    holder_kind: str = HOLDER_MEMBER
    holder_slug: str = ""
    # Where the sponsor's OWN equity lives (its Fairmint cap table, say). A link out,
    # never anything this app computes.
    holder_url: str = ""
    stakes: List[StakeProvenance] = field(default_factory=list)

    @property
    def is_sponsor(self) -> bool:
        return self.holder_kind == HOLDER_SPONSOR


@dataclass
class Pie:
    """The whole org pie: total issued equity + every holder's traceable slice.

    ``member_count`` counts people, not sponsors — it is what the page means by "across
    N members". Sponsors are counted separately in ``sponsor_count``.
    """

    org_slug: str
    unit_name: str
    total: Decimal
    member_count: int
    slices: List[PieSlice] = field(default_factory=list)
    sponsor_count: int = 0


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


def _stakes_by_holder(org) -> Dict[int, List[OrgStake]]:
    """Sponsor stakes in this org, grouped by holder id (usually zero or one holder)."""
    stakes = OrgStake.objects.filter(org=org).select_related("holder")
    by_holder: Dict[int, List[OrgStake]] = {}
    for stake in stakes:
        by_holder.setdefault(stake.holder_id, []).append(stake)
    return by_holder


def resolved_stake_values(org) -> Dict[int, Decimal]:
    """The value of every OrgStake in an org, resolving percent-of-start stakes.

    A stake with a stored value IS that value. A stake with only ``target_pct`` (a
    share of the starting split, agreed before lock-in) is resolved here, every time
    the pie is computed, so the order the team entered things in never matters. It
    resolves against the STARTING set — opening balances plus all stakes — never the
    live pie, so work earned through drops sits on top and dilutes it like everyone
    else's share. With F the fixed starting total and p the summed percent fractions,
    the starting total S satisfies S = F + p·S, so S = F / (1 − p).

    Defensively: percents summing to 100 or more resolve to zero rather than divide
    by zero or mint a negative pie (the grant form refuses such a percent, so this
    only guards corrupted data), and a percent of an empty starting set is zero.
    After lock-in every stake has a stored value (lock_pie wrote it), so this
    reduces to reading them.
    """
    stakes = list(OrgStake.objects.filter(org=org))
    values: Dict[int, Decimal] = {s.pk: s.value for s in stakes if s.value is not None}
    pct_stakes = [s for s in stakes if s.value is None]
    if not pct_stakes:
        return values

    openings = OpeningBalance.objects.filter(org=org).aggregate(t=Sum("value"))["t"] or ZERO
    fixed_total = openings + sum(values.values(), ZERO)
    pct_sum = sum((s.target_pct or ZERO for s in pct_stakes), ZERO) / ONE_HUNDRED
    if pct_sum >= Decimal("1") or fixed_total <= ZERO:
        for s in pct_stakes:
            values[s.pk] = ZERO
        return values

    starting_total = fixed_total / (Decimal("1") - pct_sum)
    for s in pct_stakes:
        values[s.pk] = _cents((s.target_pct or ZERO) / ONE_HUNDRED * starting_total)
    return values


def _sponsor_slices(org) -> List[PieSlice]:
    """One slice per sponsor, with share left at zero for the caller to derive."""
    resolved = resolved_stake_values(org)
    slices: List[PieSlice] = []
    for stakes in _stakes_by_holder(org).values():
        holder = stakes[0].holder
        total = _cents(sum((resolved[s.pk] for s in stakes), ZERO))
        slices.append(
            PieSlice(
                membership_id=None,
                member_label=holder.display_name,
                role=SPONSOR_ROLE_LABEL,
                drops_total=ZERO,
                opening_total=total,
                issued_total=total,
                share=ZERO,
                share_pct=ZERO,
                holder_kind=HOLDER_SPONSOR,
                holder_slug=holder.slug,
                holder_url=holder.url,
                stakes=[
                    StakeProvenance(
                        stake_id=s.pk,
                        value=resolved[s.pk],
                        source_note=s.source_note,
                        target_pct=s.target_pct,
                    )
                    for s in stakes
                ],
            )
        )
    return slices


def compute_pie(org) -> Pie:
    """
    Compute the current pie for an org.

    Per membership: issued_total = Σ final_value of DropLines in APPROVED DropRuns
    + Σ OpeningBalance.value. Each member's share is issued_total / org total.

    Any sponsor holding OrgStake rows in this org joins the same total as an extra slice
    (see :func:`_sponsor_slices`), so it dilutes exactly as a member's does when new work
    is issued. Orgs with no sponsor are entirely unaffected.

    Returns a :class:`Pie` whose slices are sorted by issued_total (desc, then label) and
    carry full provenance (the exact lines/tasks/opening balances/stakes) for drill-down.
    The zero-total org is handled without dividing by zero (all shares are 0).
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

    sponsor_slices = _sponsor_slices(org)
    for s in sponsor_slices:
        total += s.issued_total
    slices.extend(sponsor_slices)

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
        sponsor_count=len(sponsor_slices),
    )


def value_for_target_share(org, target_pct: Decimal) -> Decimal:
    """How much to grant NOW so the holder ends up owning ``target_pct`` of the pie.

    A share is a fraction of the whole, and the grant is part of the whole, so this is
    not ``total * pct``. To hold p percent once the grant lands::

        v / (total + v) = p / 100   =>   v = total * p / (100 - p)

    Stating a percent and letting this do the arithmetic is the point: an admin who
    means "half" should not have to work out that half of a 50,000-slice pie is another
    50,000 slices, not 25,000.

    Raises ValueError when the pie is empty (any grant would be 100% of it, so a
    percentage says nothing) or when the target is not below 100 (unreachable: the
    members already hold what they hold, and this never takes anything away).
    """
    total = compute_pie(org).total
    if total <= ZERO:
        raise ValueError("This pie is empty, so a percentage of it means nothing yet.")
    if target_pct <= ZERO or target_pct >= Decimal("100"):
        raise ValueError("A share has to be above 0 and below 100 percent.")
    value = total * target_pct / (Decimal("100") - target_pct)
    return _cents(value)


# --------------------------------------------------------------------------- #
# Lock-in — "nothing is final until lock-in by majority decision."
#
# The pie launches on an adjustable starting split; locking it in makes it the
# record. The decision is a work-weighted majority vote (apps.votes): weight is what
# each member has actually earned plus their starting balance — "your share of the
# winnings should reflect your share of the bets" (Slicing Pie). Money doesn't vote:
# sponsor stakes hold no membership and are never in the electorate.
# --------------------------------------------------------------------------- #
LOCK_YES = "Lock it in"
LOCK_NO = "Not yet"
LOCK_QUESTION = "Lock in the starting split as the record?"


class LockError(Exception):
    """A pie lock-in rule was violated (wrong phase, or a vote already running)."""


def current_lock_vote(org):
    """The live lock-in attempt for an org, or None."""
    from .models import PieLockVote

    return (
        PieLockVote.objects.filter(org=org, vote__closed_at__isnull=True)
        .select_related("vote")
        .first()
    )


def lock_progress(vote) -> dict:
    """Where a lock-in vote stands, against the WHOLE electorate.

    Majority means the weight cast for locking exceeds half of ALL snapshotted
    weight — an absolute majority of the members' stake, not of turnout. When the
    whole electorate's weight is zero (a brand-new org with nothing entered yet),
    heads count instead so the team is never wedged.
    """
    from apps.votes.services import tally as vote_tally

    t = vote_tally(vote)
    total_weight = sum((Decimal(w) for w in vote.weight_snapshot.values()), ZERO)
    yes = next((r for r in t.results if r.option == LOCK_YES), None)
    yes_weight = yes.weighted if yes else ZERO
    yes_raw = yes.raw if yes else 0
    electorate = len(vote.weight_snapshot)
    if total_weight > ZERO:
        majority = yes_weight * 2 > total_weight
        yes_pct = (yes_weight / total_weight * ONE_HUNDRED).quantize(PCT_Q)
    else:
        majority = yes_raw * 2 > electorate
        yes_pct = (
            (Decimal(yes_raw) / Decimal(electorate) * ONE_HUNDRED).quantize(PCT_Q)
            if electorate
            else ZERO
        )
    return {
        "total_weight": total_weight,
        "yes_weight": yes_weight,
        "yes_pct": yes_pct,
        "majority": majority,
        "ballots_cast": t.raw_total,
        "electorate": electorate,
    }


@transaction.atomic
def start_lock_vote(org):
    """Open the lock-in vote for a launched pie. One live attempt at a time."""
    from apps.votes.services import create_vote, open_vote

    from .models import PieLockVote

    if org.pie_phase != PiePhase.LAUNCHED:
        raise LockError("Only a launched pie can be locked in.")
    if current_lock_vote(org) is not None:
        raise LockError("A lock-in vote is already running.")
    vote = create_vote(org, LOCK_QUESTION, [LOCK_YES, LOCK_NO])
    open_vote(vote)
    return PieLockVote.objects.create(org=org, vote=vote)


@transaction.atomic
def cast_lock_ballot(lock, membership, choice: str):
    """Record a member's lock-in ballot; the moment a majority is reached, lock.

    Re-voting replaces the earlier ballot (apps.votes semantics). Returns the lock
    row, whose ``locked`` flag says whether this ballot carried the decision.
    """
    from apps.votes.services import cast_ballot, close_vote

    cast_ballot(lock.vote, membership, choice)
    if lock_progress(lock.vote)["majority"]:
        close_vote(lock.vote)
        lock.locked = True
        lock.save(update_fields=["locked"])
        lock_pie(lock.org)
    return lock


@transaction.atomic
def close_lock_vote(lock):
    """Close a lock-in vote early. If a majority is already there, it locks;
    otherwise the pie stays launched and adjustable, and the team can try again."""
    from apps.votes.services import close_vote

    close_vote(lock.vote)
    if lock_progress(lock.vote)["majority"]:
        lock.locked = True
        lock.save(update_fields=["locked"])
        lock_pie(lock.org)
    return lock


@transaction.atomic
def lock_pie(org):
    """Make the starting split the record.

    Percent stakes get their resolved value written down (the percent stays as
    provenance), and the org moves to LOCKED. From here on every grant is an add:
    new value in, new units out, everyone dilutes — the dynamic model, exactly as
    the book runs it.
    """
    resolved = resolved_stake_values(org)
    for stake in OrgStake.objects.filter(org=org, value__isnull=True):
        stake.value = _cents(resolved[stake.pk])
        stake.save(update_fields=["value"])
    org.pie_phase = PiePhase.LOCKED
    org.pie_locked_at = timezone.now()
    org.save(update_fields=["pie_phase", "pie_locked_at", "updated_at"])
    return org


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
