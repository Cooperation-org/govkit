"""
Work-weighted meeting votes — service layer (shared by the HTML views and the DRF API).

These are **informal live direction votes** for meetings, not binding elections. The flow:

    create (draft) -> open (snapshot the weights) -> members cast ballots -> close

The weight snapshot is the whole point: when a vote opens we freeze every member's
:func:`work_weight` into ``Vote.weight_snapshot`` so the weighted tally is computed from
the electorate *as it stood at open time*. Earnings that land later never change a vote's
result — the vote is auditable and reproducible from its own snapshot.

Raw one-member-one-vote counts are always kept alongside the weighted tally for
transparency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional

from django.db import transaction
from django.utils import timezone

from apps.orgs.weighting import work_weight_map

from .models import Ballot, Vote

ZERO = Decimal("0")


class VoteError(Exception):
    """A vote-lifecycle rule was violated (draft/open/closed state or an invalid choice)."""


# --------------------------------------------------------------------------- #
# Lifecycle state helpers (derived, since the frozen model has no state field).
#
# opened_at is auto-set at creation, so it marks creation time; the operative "opened"
# signal is a populated weight_snapshot. A vote is:
#   * draft   — not yet snapshotted, not closed        (created, awaiting open)
#   * live    — snapshotted and not closed             (accepting ballots)
#   * closed  — closed_at set                          (final)
# --------------------------------------------------------------------------- #
def is_draft(vote: Vote) -> bool:
    return not vote.weight_snapshot and vote.closed_at is None


def is_live(vote: Vote) -> bool:
    return bool(vote.weight_snapshot) and vote.closed_at is None


def vote_status(vote: Vote) -> str:
    if vote.closed_at is not None:
        return "closed"
    return "live" if vote.weight_snapshot else "draft"


# --------------------------------------------------------------------------- #
# Lifecycle actions.
# --------------------------------------------------------------------------- #
def create_vote(org, question: str, options: List[str]) -> Vote:
    """Create a draft vote (question + option list). No snapshot yet — see :func:`open_vote`."""
    cleaned = [str(o).strip() for o in options if str(o).strip()]
    if not (question or "").strip():
        raise VoteError("A vote needs a question.")
    if len(cleaned) < 2:
        raise VoteError("A vote needs at least two options.")
    if len(set(cleaned)) != len(cleaned):
        raise VoteError("Options must be distinct.")
    return Vote.objects.create(org=org, question=question.strip(), options=cleaned)


def _snapshot_weights(org) -> dict:
    """Freeze every member's work weight as JSON: {str(membership_id): str(weight)}."""
    weights = work_weight_map(org, org.valuation_config.weight_window)
    return {str(mid): str(w) for mid, w in weights.items()}


@transaction.atomic
def open_vote(vote: Vote) -> Vote:
    """Open a draft vote: snapshot the electorate's weights so the tally is reproducible.

    Idempotency is refused deliberately — re-opening would silently re-snapshot and change
    a live vote's basis. Raises if the vote is not a fresh draft or has no eligible members.
    """
    if vote.closed_at is not None:
        raise VoteError("This vote is already closed.")
    if vote.weight_snapshot:
        raise VoteError("This vote is already open.")
    snapshot = _snapshot_weights(vote.org)
    if not snapshot:
        raise VoteError("Cannot open a vote in an org with no members.")
    vote.weight_snapshot = snapshot
    vote.save(update_fields=["weight_snapshot"])
    return vote


@transaction.atomic
def cast_ballot(vote: Vote, membership, choice: str) -> Ballot:
    """Record (or replace) a member's ballot. One ballot per member; re-voting replaces it.

    Only members captured in the open-time snapshot may vote — the electorate is frozen at
    open. Raises unless the vote is live and the choice is one of the vote's options.
    """
    if not is_live(vote):
        raise VoteError("This vote is not open for voting.")
    if choice not in vote.options:
        raise VoteError("That choice is not one of the vote's options.")
    if str(membership.id) not in vote.weight_snapshot:
        raise VoteError("Only members eligible when the vote opened may vote.")
    ballot, _created = Ballot.objects.update_or_create(
        vote=vote,
        membership=membership,
        defaults={"org": vote.org, "choice": choice, "cast_at": timezone.now()},
    )
    return ballot


@transaction.atomic
def close_vote(vote: Vote) -> Vote:
    """Close a live vote. The tally is already reproducible from the snapshot; this just
    stops further ballots and moves the vote into history."""
    if vote.closed_at is not None:
        raise VoteError("This vote is already closed.")
    if not vote.weight_snapshot:
        raise VoteError("Cannot close a vote that was never opened.")
    vote.closed_at = timezone.now()
    vote.save(update_fields=["closed_at"])
    return vote


# --------------------------------------------------------------------------- #
# Tally — always from the snapshot, never from live weights.
# --------------------------------------------------------------------------- #
@dataclass
class OptionResult:
    option: str
    weighted: Decimal  # Σ snapshot weight of members who chose this option
    raw: int  # one-member-one-vote count
    weighted_pct: Decimal  # share of the cast weighted total (0..100)
    raw_pct: Decimal  # share of the cast raw total (0..100)


@dataclass
class Tally:
    """A vote's result: weighted (from the open-time snapshot) alongside raw counts."""

    vote_id: int
    question: str
    status: str
    weighted_total: Decimal  # total weight actually cast (voters only)
    raw_total: int  # total ballots cast
    results: List[OptionResult] = field(default_factory=list)
    winner: Optional[str] = None  # by weighted total; None on an empty/tied-at-zero vote


def _snapshot_weight(vote: Vote, membership_id: int) -> Decimal:
    raw = vote.weight_snapshot.get(str(membership_id))
    return Decimal(raw) if raw is not None else ZERO


def tally(vote: Vote) -> Tally:
    """Compute the weighted + raw tally from the open-time snapshot.

    Weighted contribution of each ballot is the voter's weight *as snapshotted when the
    vote opened* — so later earnings cannot change a result. Members who did not vote do
    not count toward either total. Percentages are of the cast totals (turnout-relative).
    """
    ballots = list(vote.ballots.all())
    weighted_by_option = {opt: ZERO for opt in vote.options}
    raw_by_option = {opt: 0 for opt in vote.options}

    for b in ballots:
        if b.choice not in weighted_by_option:
            # A choice no longer in options (options are immutable in practice) — skip
            # defensively so a stray ballot can't crash the tally.
            weighted_by_option[b.choice] = ZERO
            raw_by_option[b.choice] = 0
        weighted_by_option[b.choice] += _snapshot_weight(vote, b.membership_id)
        raw_by_option[b.choice] += 1

    weighted_total = sum(weighted_by_option.values(), ZERO)
    raw_total = sum(raw_by_option.values())

    results: List[OptionResult] = []
    for opt in weighted_by_option:
        w = weighted_by_option[opt]
        r = raw_by_option[opt]
        results.append(
            OptionResult(
                option=opt,
                weighted=w,
                raw=r,
                weighted_pct=(
                    (w / weighted_total * Decimal("100")).quantize(Decimal("0.01"))
                    if weighted_total > ZERO
                    else ZERO
                ),
                raw_pct=(
                    (Decimal(r) / Decimal(raw_total) * Decimal("100")).quantize(Decimal("0.01"))
                    if raw_total
                    else ZERO
                ),
            )
        )

    # Winner by weighted total (the point of a work-weighted vote); None if nothing counted.
    winner = None
    if weighted_total > ZERO:
        top = max(results, key=lambda x: x.weighted)
        # Only call it a winner if it's a strict maximum.
        if sum(1 for x in results if x.weighted == top.weighted) == 1:
            winner = top.option

    # Present options in the order the vote defined them.
    results.sort(key=lambda x: vote.options.index(x.option) if x.option in vote.options else 999)

    return Tally(
        vote_id=vote.pk,
        question=vote.question,
        status=vote_status(vote),
        weighted_total=weighted_total,
        raw_total=raw_total,
        results=results,
        winner=winner,
    )
