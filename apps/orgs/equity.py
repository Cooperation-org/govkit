"""Setting a starting value to a number, without losing what it was.

A member's starting stake is not a field. It is the SUM of their OpeningBalance rows,
and a sponsor's is the sum of their OrgStake rows — both append-only, so every route
that has ever set a starting value (setup, an admin grant, a CSV import) added a row
and none of them can lower a total.

This module closes that gap the same way the tables already work: setting a total to N
APPENDS one more row for the difference, negative when the total comes down. The sum
lands on N, the earlier rows stay exactly as they were, and the new row says who
changed it, when, and what it was before. Nothing is rewritten and nothing is deleted,
so the trace on the pie page reads as a history rather than a number that moved.

A sponsor stake can be recorded as a percent of the starting split instead of an
amount (see OrgStake), and a percent is re-resolved every time the pie is computed.
Typing an amount over one would not stick, so a percent is adjusted as a percent: the
adjustment row carries the difference in ``target_pct``. Each kind is corrected in its
own kind.

Refused once the pie is LOCKED — the starting split is the record from there, and new
value enters as a grant that dilutes everyone (apps.orgs.views.sponsor_grant).
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import ExternalHolder, Membership, OpeningBalance, OrgStake, PiePhase

ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")
CENTS = Decimal("0.01")


class EquityLocked(Exception):
    """The pie is locked in, so the starting split cannot be adjusted."""


class EquityRefused(Exception):
    """The requested total is not one the starting split can hold."""


def _require_adjustable(org) -> None:
    if org.pie_phase == PiePhase.LOCKED:
        raise EquityLocked(
            "This pie is locked in — the starting split is the record. "
            "Value can still be added as a grant, which dilutes everyone."
        )


def _note(by_user, was: str) -> str:
    """The provenance line on an adjustment row: who, when, and what it was."""
    who = by_user.get_short_name() if by_user is not None else "an admin"
    return f"Adjusted by {who} {timezone.localdate():%Y-%m-%d}, was {was}"


# --------------------------------------------------------------------------- #
# Members — OpeningBalance rows.
# --------------------------------------------------------------------------- #
def member_starting_total(org, membership: Membership) -> Decimal:
    """What a member's starting stake currently sums to."""
    total = OpeningBalance.objects.filter(org=org, membership=membership).aggregate(t=Sum("value"))[
        "t"
    ]
    return (total or ZERO).quantize(CENTS)


@transaction.atomic
def set_member_starting_total(org, membership: Membership, new_total: Decimal, by_user=None):
    """Make a member's starting stake sum to ``new_total`` by appending the difference.

    Returns the adjustment row, or None when the total is already that number (there
    is nothing to record, and an empty row would be noise in the trace).
    """
    _require_adjustable(org)
    if new_total < ZERO:
        raise EquityRefused("A starting value cannot be below zero.")
    new_total = new_total.quantize(CENTS)
    old_total = member_starting_total(org, membership)
    delta = new_total - old_total
    if delta == ZERO:
        return None
    return OpeningBalance.objects.create(
        org=org,
        membership=membership,
        value=delta,
        source_note=_note(by_user, f"{old_total}"),
        is_adjustment=True,
    )


# --------------------------------------------------------------------------- #
# Sponsors — OrgStake rows, in whichever kind the stake was recorded.
# --------------------------------------------------------------------------- #
def sponsor_fixed_total(org, holder: ExternalHolder) -> Decimal:
    """The amount a holder's fixed-value stakes sum to (percent stakes excluded)."""
    total = OrgStake.objects.filter(org=org, holder=holder, value__isnull=False).aggregate(
        t=Sum("value")
    )["t"]
    return (total or ZERO).quantize(CENTS)


def sponsor_pct_total(org, holder: ExternalHolder) -> Decimal:
    """The percent of the starting split a holder's percent stakes sum to."""
    total = OrgStake.objects.filter(org=org, holder=holder, value__isnull=True).aggregate(
        t=Sum("target_pct")
    )["t"]
    return (total or ZERO).quantize(CENTS)


@transaction.atomic
def set_sponsor_fixed_total(org, holder: ExternalHolder, new_total: Decimal, by_user=None):
    """Make a holder's fixed stake sum to ``new_total`` by appending the difference.

    Only the fixed rows are touched. A holder who also has a percent stake keeps it —
    the percent is a different record and is adjusted by :func:`set_sponsor_pct_total`.
    """
    _require_adjustable(org)
    if new_total < ZERO:
        raise EquityRefused("A stake cannot be below zero.")
    new_total = new_total.quantize(CENTS)
    old_total = sponsor_fixed_total(org, holder)
    delta = new_total - old_total
    if delta == ZERO:
        return None
    return OrgStake.objects.create(
        org=org,
        holder=holder,
        value=delta,
        target_pct=None,
        source_note=_note(by_user, f"{old_total}"),
        is_adjustment=True,
        granted_by=by_user,
    )


@transaction.atomic
def set_sponsor_pct_total(org, holder: ExternalHolder, new_pct: Decimal, by_user=None):
    """Make a holder's percent-of-the-starting-split sum to ``new_pct``.

    The same ceiling the grant form enforces applies here: percent stakes describe
    shares of one starting split, so together they cannot promise out the whole of it.
    """
    _require_adjustable(org)
    if new_pct < ZERO:
        raise EquityRefused("A share cannot be below zero percent.")
    new_pct = new_pct.quantize(CENTS)
    old_pct = sponsor_pct_total(org, holder)
    others = (
        OrgStake.objects.filter(org=org, value__isnull=True)
        .exclude(holder=holder)
        .aggregate(t=Sum("target_pct"))["t"]
        or ZERO
    )
    if others + new_pct >= ONE_HUNDRED:
        raise EquityRefused(
            f"Percent stakes already promise {others}% of the starting split — this "
            "would take it to 100% or more, leaving the team nothing."
        )
    delta = new_pct - old_pct
    if delta == ZERO:
        return None
    return OrgStake.objects.create(
        org=org,
        holder=holder,
        value=None,
        target_pct=delta,
        source_note=_note(by_user, f"{old_pct}% of the starting split"),
        is_adjustment=True,
        granted_by=by_user,
    )
