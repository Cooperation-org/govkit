"""
Drop valuation services — SIGNATURE STUB for the drops agent.

`compute_line_value` is the single documented place where a member's earned value for a
run is computed from their eligible tasks. The drops agent implements the body; the
signature + contract below are the seam other code depends on.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable


def compute_line_value(membership, tasks: Iterable, valuation_config) -> Decimal:
    """
    Compute the pre-adjustment earned value for one member in a drop run.

    Two modes (from valuation_config.valuation_mode):

      hours_rate:
          earned = Σ over tasks of (rate × hours − cash_paid)
          where rate = membership.effective_rate.

      direct_value:
          earned = Σ over tasks of the direct value tagged on each task
          (parsed per the source's value_tag_pattern; stored on TrackedTask.claimed_value).

    The result is then multiplied by the org's at-risk multiplier
    (valuation_config.at_risk_multiplier_noncash / _cash as appropriate; default 1.0).

    Args:
        membership: orgs.Membership the line belongs to.
        tasks: iterable of tasksources.TrackedTask eligible for this member/run.
        valuation_config: orgs.ValuationConfig for the org.

    Returns:
        Decimal: the computed value BEFORE any steward adjustment. The caller stores this
        as DropLine.computed_value; final_value = computed_value + adjustment.
    """
    raise NotImplementedError(
        "Drops agent: implement both valuation modes + at-risk multiplier here."
    )
