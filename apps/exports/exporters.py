"""
EquityExport adapters — pluggable export targets for the pie / contribution record.

Equity only ever *leaves* GovKit through an exporter (settled design decision #2). Each
exporter is a small adapter that turns the org's frozen records into rows for one target
format. Generic CSV is the baseline; the Slicing Pie contribution format is the second
target; a Fairmint (or any other) exporter slots in by subclassing `EquityExport` and
registering it in `EXPORTERS` — no other code changes.

Export figures are computed *directly from the frozen models* here (a minimal local
re-implementation of the pie sum, so this app does not import the concurrently-built
`apps.pie`). The rule that everything traces back to the work that earned it holds: a
membership's issued total is `Σ final_value` over drop lines in APPROVED runs, plus
`Σ value` over opening balances.
"""

from __future__ import annotations

import abc
import csv
import io
from decimal import Decimal
from typing import Iterable

from apps.drops.models import DropLine, DropRunState
from apps.orgs.models import Membership, OpeningBalance

ZERO = Decimal("0")


# --------------------------------------------------------------------------- #
# Minimal local pie computation (do NOT import apps.pie — built in parallel).
# --------------------------------------------------------------------------- #
def issued_drop_total(org, membership) -> Decimal:
    """Σ final_value over this membership's drop lines in APPROVED runs (issued equity)."""
    total = (
        DropLine.objects.for_org(org)
        .filter(membership=membership, run__state=DropRunState.APPROVED)
        .aggregate(models_sum=_sum("final_value"))["models_sum"]
    )
    return total or ZERO


def opening_balance_total(org, membership) -> Decimal:
    """Σ value over this membership's imported opening balances."""
    total = OpeningBalance.objects.filter(org=org, membership=membership).aggregate(
        models_sum=_sum("value")
    )["models_sum"]
    return total or ZERO


def _sum(field):
    # Local import keeps the module import graph tiny and explicit.
    from django.db.models import Sum

    return Sum(field)


def membership_totals(org) -> list[dict]:
    """
    Per-membership equity for the org: issued drops + opening balances, and the share
    of the org total. Returned sorted by descending total for stable, readable output.
    """
    rows = []
    grand_total = ZERO
    for membership in Membership.objects.filter(org=org).select_related("user"):
        issued = issued_drop_total(org, membership)
        opening = opening_balance_total(org, membership)
        total = issued + opening
        if total == ZERO and issued == ZERO and opening == ZERO:
            # Skip members with no equity at all — nothing to export for them.
            continue
        grand_total += total
        rows.append(
            {
                "membership": membership,
                "issued_value": issued,
                "opening_balance": opening,
                "total_value": total,
            }
        )
    for row in rows:
        row["share"] = (row["total_value"] / grand_total) if grand_total > ZERO else ZERO
    rows.sort(key=lambda r: r["total_value"], reverse=True)
    return rows


def _member_identifier(membership) -> str:
    """Stable human identifier for a member — email (identity map is explicit, never a name)."""
    return membership.user.email


# --------------------------------------------------------------------------- #
# Adapter interface
# --------------------------------------------------------------------------- #
class EquityExport(abc.ABC):
    """
    Base class for an equity-export target.

    A subclass declares `format_key` (stable slug used in URLs/filenames) and
    `label` (human name), and implements `fieldnames()` + `rows(org)`. `to_csv(org)`
    renders those into a CSV string; `download_filename(org)` names the file. Add a new
    target by subclassing and registering in `EXPORTERS`.
    """

    format_key: str = ""
    label: str = ""

    @abc.abstractmethod
    def fieldnames(self) -> list[str]:
        """Ordered CSV column names."""

    @abc.abstractmethod
    def rows(self, org) -> Iterable[dict]:
        """Yield one dict per output row, keyed by `fieldnames()`."""

    def download_filename(self, org) -> str:
        return f"{org.slug}-{self.format_key}.csv"

    def to_csv(self, org) -> str:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=self.fieldnames(), extrasaction="ignore")
        writer.writeheader()
        for row in self.rows(org):
            writer.writerow(row)
        return buffer.getvalue()


class GenericPieExport(EquityExport):
    """Generic, tool-neutral snapshot of the current pie: one row per member with equity."""

    format_key = "generic"
    label = "Generic CSV (current pie)"

    def fieldnames(self) -> list[str]:
        return [
            "member_email",
            "taiga_user_id",
            "issued_value",
            "opening_balance",
            "total_value",
            "share",
            "unit",
        ]

    def rows(self, org) -> Iterable[dict]:
        for row in membership_totals(org):
            membership = row["membership"]
            yield {
                "member_email": _member_identifier(membership),
                "taiga_user_id": membership.taiga_user_id or "",
                "issued_value": _money(row["issued_value"]),
                "opening_balance": _money(row["opening_balance"]),
                "total_value": _money(row["total_value"]),
                "share": _ratio(row["share"]),
                "unit": org.unit_name,
            }


class SlicingPieExport(EquityExport):
    """
    Contribution export compatible with the Slicing Pie model.

    Slicing Pie values contributions as `hours × rate` (adjusted by an at-risk
    multiplier), with cash contributions offsetting/earning at their own multiplier. We
    are ALLIES of Slicing Pie: this exporter surfaces each *contribution* (an issued drop
    line, plus each opening balance as a historical contribution) as a row, with the
    org's at-risk multipliers exposed as config-driven columns and a cash-offset column.

    Per-line `hours` and `cash_offset` are summed from the tracked tasks the drop line was
    computed from (its traceability links); when a line has no linked tasks those columns
    are blank. `slices` is the line's `final_value` — the at-risk-adjusted value already
    computed by the drop engine — so this stays a faithful re-export of the record of
    truth rather than a re-derivation.
    """

    format_key = "slicing_pie"
    label = "Slicing Pie contribution format"

    def fieldnames(self) -> list[str]:
        return [
            "member_email",
            "taiga_user_id",
            "contribution_type",
            "contribution_ref",
            "contribution_date",
            "hours",
            "rate",
            "cash_offset",
            "at_risk_multiplier_noncash",
            "at_risk_multiplier_cash",
            "base_value",
            "adjustment",
            "slices",
            "unit",
        ]

    def rows(self, org) -> Iterable[dict]:
        config = getattr(org, "valuation_config", None)
        m_noncash = config.at_risk_multiplier_noncash if config else Decimal("1.0")
        m_cash = config.at_risk_multiplier_cash if config else Decimal("1.0")

        # Issued work contributions: drop lines in approved runs, newest first.
        lines = (
            DropLine.objects.for_org(org)
            .filter(run__state=DropRunState.APPROVED)
            .select_related("run", "membership", "membership__user")
            .prefetch_related("tasks")
            .order_by("-run__approved_at", "membership_id")
        )
        for line in lines:
            hours = ZERO
            cash = ZERO
            has_task_hours = False
            has_task_cash = False
            for task in line.tasks.all():
                if task.hours is not None:
                    hours += task.hours
                    has_task_hours = True
                if task.cash is not None:
                    cash += task.cash
                    has_task_cash = True
            rate = line.membership.effective_rate
            approved = line.run.approved_at
            yield {
                "member_email": _member_identifier(line.membership),
                "taiga_user_id": line.membership.taiga_user_id or "",
                "contribution_type": "work_drop",
                "contribution_ref": f"drop_line:{line.pk} run:{line.run_id}",
                "contribution_date": approved.date().isoformat() if approved else "",
                "hours": _money(hours) if has_task_hours else "",
                "rate": _money(rate) if rate is not None else "",
                "cash_offset": _money(cash) if has_task_cash else "",
                "at_risk_multiplier_noncash": _ratio(m_noncash, places=3),
                "at_risk_multiplier_cash": _ratio(m_cash, places=3),
                "base_value": _money(line.computed_value),
                "adjustment": _money(line.adjustment),
                "slices": _money(line.final_value),
                "unit": org.unit_name,
            }

        # Historical contributions: imported opening balances.
        balances = (
            OpeningBalance.objects.filter(org=org)
            .select_related("membership", "membership__user")
            .order_by("-created_at")
        )
        for balance in balances:
            yield {
                "member_email": _member_identifier(balance.membership),
                "taiga_user_id": balance.membership.taiga_user_id or "",
                "contribution_type": "opening_balance",
                "contribution_ref": f"opening_balance:{balance.pk}"
                + (f" ({balance.source_note})" if balance.source_note else ""),
                "contribution_date": balance.created_at.date().isoformat(),
                "hours": "",
                "rate": "",
                "cash_offset": "",
                "at_risk_multiplier_noncash": _ratio(m_noncash, places=3),
                "at_risk_multiplier_cash": _ratio(m_cash, places=3),
                "base_value": _money(balance.value),
                "adjustment": _money(ZERO),
                "slices": _money(balance.value),
                "unit": org.unit_name,
            }


# Registry — add a target (e.g. Fairmint) by subclassing EquityExport + one line here.
EXPORTERS = {e.format_key: e for e in (GenericPieExport(), SlicingPieExport())}


def get_exporter(format_key: str) -> EquityExport:
    """Look up a registered exporter by key. Raises KeyError for an unknown format."""
    return EXPORTERS[format_key]


def _money(value: Decimal) -> str:
    """Format a monetary/equity amount with 2 decimal places."""
    return f"{Decimal(value):.2f}"


def _ratio(value: Decimal, places: int = 6) -> str:
    """Format a share/multiplier ratio."""
    return f"{Decimal(value):.{places}f}"
