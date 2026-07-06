"""
Work-weight core — the single place that turns issued earnings into governance weight.

Both work-weighted **votes** (apps.votes) and work-weighted **sortition**
(apps.sortition) derive a member's influence from the same earnings record the pie is
built on, so weight always means "value this member has actually earned".

Definition (settled — see the build contract, items 10/11):

    work_weight(member) = Σ DropLine.final_value over lines in APPROVED runs
                          + Σ OpeningBalance.value

filtered by the org's **weight window**:

  * ``all_time``      — every approved run counts.
  * ``trailing_12m``  — only lines whose run was approved within the last 12 months.

Opening balances are imported historical equity with no run/approval date, so they always
count as all-time regardless of the window (they represent standing a team started with).

All arithmetic is Decimal-precise; a member who has issued nothing has a weight of exactly
``Decimal("0")``.

This module lives in ``orgs`` (the tenancy core every app already depends on) rather than
inside ``drops`` so both feature apps can import it without reaching into another feature
app; it reads ``drops`` / ``orgs`` models the same way ``pie.services`` does.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Dict

from django.db.models import Sum
from django.utils import timezone

from apps.drops.models import DropLine, DropRunState

from .models import Membership, OpeningBalance, WeightWindow

ZERO = Decimal("0")
# "Trailing 12 months" is measured as the last 365 days up to now.
TRAILING_DAYS = 365


def _window_cutoff(window):
    """The earliest ``approved_at`` that counts for a window, or None for all-time."""
    if window == WeightWindow.TRAILING_12M:
        return timezone.now() - timedelta(days=TRAILING_DAYS)
    return None


def _approved_lines(org, window):
    """Approved drop lines for an org, filtered to the window on the run's approval time."""
    qs = DropLine.objects.for_org(org).filter(run__state=DropRunState.APPROVED)
    cutoff = _window_cutoff(window)
    if cutoff is not None:
        qs = qs.filter(run__approved_at__gte=cutoff)
    return qs


def work_weight(org, membership, window) -> Decimal:
    """
    One member's work weight in an org for the given weight window.

    Args:
        org: the orgs.Org the weight is scoped to.
        membership: the orgs.Membership to weigh.
        window: an orgs.WeightWindow value (``all_time`` / ``trailing_12m``).

    Returns:
        Decimal: Σ issued DropLine.final_value (windowed) + Σ OpeningBalance.value
        (all-time). Zero (``Decimal("0")``) when the member has issued nothing.
    """
    lines_total = (
        _approved_lines(org, window)
        .filter(membership=membership)
        .aggregate(t=Sum("final_value"))["t"]
        or ZERO
    )
    opening_total = (
        OpeningBalance.objects.filter(org=org, membership=membership).aggregate(t=Sum("value"))["t"]
        or ZERO
    )
    return lines_total + opening_total


def work_weight_map(org, window) -> Dict[int, Decimal]:
    """
    The full ``{membership_id: weight}`` map for every member of an org.

    Every membership is present (a member with zero issued earnings maps to
    ``Decimal("0")``), so callers that snapshot the map — votes at open time, sortition at
    draw time — capture the whole electorate, not just those who have earned. Computed in a
    fixed number of queries (two aggregates) regardless of member count.
    """
    weights: Dict[int, Decimal] = {
        mid: ZERO for mid in Membership.objects.filter(org=org).values_list("id", flat=True)
    }
    for row in (
        _approved_lines(org, window).values("membership_id").annotate(total=Sum("final_value"))
    ):
        weights[row["membership_id"]] = weights.get(row["membership_id"], ZERO) + (
            row["total"] or ZERO
        )
    for row in (
        OpeningBalance.objects.filter(org=org).values("membership_id").annotate(total=Sum("value"))
    ):
        weights[row["membership_id"]] = weights.get(row["membership_id"], ZERO) + (
            row["total"] or ZERO
        )
    return weights
