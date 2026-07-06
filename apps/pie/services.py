"""
Pie computation — SIGNATURE STUB for the pie agent.

Definition (settled):

    Pie = Σ issued DropLines + OpeningBalances, per membership, per org.

`compute_pie` is the single documented place that turns the earnings record into shares.
The pie agent implements the body; the signature + contract are the seam.
"""

from __future__ import annotations


def compute_pie(org):
    """
    Compute current shares for an org.

    Sums, per membership:
      * final_value of DropLines belonging to APPROVED (issued) DropRuns, plus
      * OpeningBalance values,
    then derives each membership's fractional share of the org total.

    Every slice must remain traceable back to the DropLines/tasks (and opening balances)
    that produced it — return enough structure for the UI to drill down.

    Args:
        org: orgs.Org to compute the pie for.

    Returns:
        A structure of per-membership totals and shares (shape decided by the pie agent),
        scoped strictly to `org`.
    """
    raise NotImplementedError("Pie agent: implement issued-earnings aggregation + shares.")
