"""
Computed money view of a project — the one place the arithmetic lives.

Everything here is derived: budget from the Deal, paid-out from Payout rows, promised
amounts from Split percents. No stored totals anywhere. All amounts leave this module
as quantized decimal strings ("1600.00") so JSON consumers never see floats.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum

CENT = Decimal("0.01")


def _cents(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def project_summary(project):
    """The project's money picture: budget, paid out, and per-member standing.

    Members appear if they hold a split or have received a payout. `promised` is
    percent-of-budget; `remaining` is promised minus paid. Budget-derived figures are
    None when the project has no deal.
    """
    deal = getattr(project, "deal", None)
    budget = deal.budget_total if deal else None

    paid_by_membership = {
        row["membership_id"]: row["total"]
        for row in project.payouts.values("membership_id").annotate(total=Sum("amount"))
    }
    splits = list(deal.splits.select_related("membership__user")) if deal else []

    members = []
    for split in splits:
        paid = paid_by_membership.get(split.membership_id, Decimal("0"))
        promised = _cents(budget * split.percent / 100)
        members.append(
            {
                "membership_id": split.membership_id,
                "name": split.membership.user.get_username(),
                "percent": str(split.percent),
                "promised": str(promised),
                "paid_out": str(_cents(paid)),
                "remaining": str(_cents(promised - paid)),
            }
        )

    # Members who were paid but hold no split still show up — a payout without a
    # promise is exactly the kind of fact this view exists to surface.
    split_membership_ids = {s.membership_id for s in splits}
    payout_only = {}
    for payout in project.payouts.exclude(membership_id__in=split_membership_ids).select_related(
        "membership__user"
    ):
        payout_only.setdefault(payout.membership_id, payout.membership)
    for membership in payout_only.values():
        members.append(
            {
                "membership_id": membership.id,
                "name": membership.user.get_username(),
                "percent": None,
                "promised": None,
                "paid_out": str(_cents(paid_by_membership[membership.id])),
                "remaining": None,
            }
        )

    paid_total = _cents(sum(paid_by_membership.values(), Decimal("0")))
    return {
        "project": project.slug,
        "kind": project.kind,
        "status": project.status,
        "currency": deal.currency if deal else None,
        "budget_total": str(_cents(budget)) if budget is not None else None,
        "paid_out_total": str(paid_total),
        "budget_remaining": str(_cents(budget - paid_total)) if budget is not None else None,
        "members": members,
    }
