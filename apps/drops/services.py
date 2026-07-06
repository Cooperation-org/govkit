"""
Drop valuation + run-lifecycle services.

`compute_line_value` is the single documented place where a member's earned value for a
run is computed from their eligible tasks. The lifecycle helpers (`open_run`,
`adjust_line`, `approve_run`, `review_queue`) orchestrate the steward flow on top of it,
keeping the views and the DRF viewsets thin and sharing one code path (API-first).
"""

from __future__ import annotations

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

from django.db import transaction
from django.utils import timezone

from apps.orgs.models import ValuationMode
from apps.tasksources.models import TaskSourceConfig, TrackedTask

from .models import DropLine, DropRun, DropRunState

TWO_PLACES = Decimal("0.01")


def _d(value) -> Decimal:
    """Coerce a possibly-None numeric to Decimal, treating None as 0."""
    return Decimal(value) if value is not None else Decimal("0")


def compute_line_value(membership, tasks: Iterable, valuation_config) -> Decimal:
    """
    Compute the pre-adjustment earned value for one member in a drop run.

    Two modes (from valuation_config.valuation_mode):

      hours_rate:
          For each task the sweat contribution is ``rate x hours`` (rate =
          membership.effective_rate; a missing rate values sweat at 0 so the steward
          corrects it via an adjustment) and the cash offset is the cash already paid on
          the task. The at-risk multipliers are applied per resource type:

              task_value = rate*hours * at_risk_multiplier_noncash
                           - cash_paid * at_risk_multiplier_cash

      direct_value:
          For each task the value is the direct value tagged on it (stored on
          TrackedTask.claimed_value), a non-cash contribution:

              task_value = claimed_value * at_risk_multiplier_noncash

    With the default multipliers (1.0 / 1.0) this reduces exactly to the plain contract
    formulas: ``rate*hours - cash_paid`` summed, and ``sum(claimed_value)``. The result is
    quantized to 2 decimal places (matching DropLine.computed_value) with banker-safe
    ROUND_HALF_UP.

    Args:
        membership: orgs.Membership the line belongs to.
        tasks: iterable of tasksources.TrackedTask eligible for this member/run.
        valuation_config: orgs.ValuationConfig for the org.

    Returns:
        Decimal: the computed value BEFORE any steward adjustment. The caller stores this
        as DropLine.computed_value; final_value = computed_value + adjustment.
    """
    noncash_mult = valuation_config.at_risk_multiplier_noncash or Decimal("1.0")
    cash_mult = valuation_config.at_risk_multiplier_cash or Decimal("1.0")

    total = Decimal("0")

    if valuation_config.valuation_mode == ValuationMode.HOURS_RATE:
        rate = _d(membership.effective_rate)
        for task in tasks:
            sweat = rate * _d(task.hours) * noncash_mult
            cash_offset = _d(task.cash) * cash_mult
            total += sweat - cash_offset
    else:  # DIRECT_VALUE
        for task in tasks:
            total += _d(task.claimed_value) * noncash_mult

    return total.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _eligible_done_statuses(org) -> set:
    """Union of done/eligible status slugs across the org's task sources.

    Falls back to the source model's default (done/archived/historical) when the org has
    no source configured yet, so an open run still gathers sensibly in dev.
    """
    statuses: set = set()
    for source in TaskSourceConfig.objects.for_org(org):
        statuses.update(source.done_statuses or [])
    if not statuses:
        from apps.tasksources.models import default_done_statuses

        statuses.update(default_done_statuses())
    return statuses


def eligible_tasks(org):
    """Done, assigned tasks for the org not yet linked to ANY drop line.

    Drop-line linkage (a TrackedTask.drop_lines relation) is the dedup mechanism that
    replaces the legacy ``pending_cook``/``issued_cook`` task-array trick: once a task is
    part of a run's line it can never be gathered again, so it can't be double-counted.
    Tasks missing a value are still gathered (they surface in the review queue for the
    steward to correct) — only unassigned tasks are excluded, since a line needs a member.
    """
    statuses = _eligible_done_statuses(org)
    return (
        TrackedTask.objects.for_org(org)
        .filter(status__in=statuses, assignee__isnull=False, drop_lines__isnull=True)
        .distinct()
    )


class NoEligibleTasks(Exception):
    """Raised when open_run finds nothing to drop (keeps junk empty runs out of the DB)."""


@transaction.atomic
def open_run(org, opened_by_membership=None, opened_by_user=None) -> DropRun:
    """Open a new run: gather eligible tasks, group by member, create computed lines.

    Grouping is by assignee membership (the totals view), but each line keeps its task
    links so the review queue can work BY TASK. Raises NoEligibleTasks when there is
    nothing to drop.
    """
    # M4: guard against two concurrent open_run calls gathering the same task into two
    # runs. Take a row lock on the candidate tasks, THEN re-check eligibility under the
    # lock. A racing run that linked a task first will have committed by the time this
    # transaction acquires the lock, so the task drops out of the re-checked set here.
    # (select_for_update is issued on a plain pk filter — Postgres forbids FOR UPDATE with
    # DISTINCT / the outer join that eligible_tasks uses, so we lock by pk separately.)
    candidate_ids = list(eligible_tasks(org).values_list("pk", flat=True))
    if not candidate_ids:
        raise NoEligibleTasks("No eligible done tasks to drop for this org.")
    list(TrackedTask.objects.filter(pk__in=candidate_ids).select_for_update())
    tasks = list(eligible_tasks(org).filter(pk__in=candidate_ids).select_related("assignee"))
    if not tasks:
        raise NoEligibleTasks("No eligible done tasks to drop for this org.")

    by_member = defaultdict(list)
    for task in tasks:
        by_member[task.assignee].append(task)

    run = DropRun.objects.create(
        org=org,
        opened_by=opened_by_membership,
        opened_by_user=opened_by_user,
        state=DropRunState.OPEN,
    )

    config = org.valuation_config
    for membership, member_tasks in by_member.items():
        computed = compute_line_value(membership, member_tasks, config)
        line = DropLine(
            org=org,
            run=run,
            membership=membership,
            computed_value=computed,
            adjustment=Decimal("0"),
            final_value=computed,
        )
        line.save()
        line.tasks.set(member_tasks)

    return run


def review_queue(run) -> dict:
    """Structured review payload for a run, organised for the steward.

    Returns lines grouped by member (totals view) plus the tasks-missing-value queue
    (the actionable failure mode — under-claiming — surfaced prominently). Read-only.
    """
    lines = list(
        run.lines.select_related("membership", "membership__user").prefetch_related("tasks")
    )
    missing = []
    for line in lines:
        for task in line.tasks.all():
            if task.is_missing_value:
                missing.append(task)
    return {
        "run": run,
        "lines": lines,
        "missing_value_tasks": missing,
        "total_final": sum((line.final_value for line in lines), Decimal("0")),
    }


def adjust_line(line: DropLine, adjustment: Decimal, reason: str, adjusted_by=None) -> DropLine:
    """Apply a steward adjustment to a line, recomputing final_value.

    A non-zero adjustment REQUIRES a reason (enforced by DropLine.clean(), re-run here via
    save()). Raises if the line's run is already approved (immutability). final_value is
    always computed_value + adjustment so the audit chain computed -> adjustment(+reason)
    -> final stays consistent.

    ``adjusted_by`` (the acting orgs.Membership) and ``adjusted_at`` are recorded as the
    audit trail for who changed what, but only for a non-zero adjustment; resetting the
    adjustment to zero clears them (there is no adjustment to attribute).
    """
    adjustment = Decimal(adjustment)
    line.adjustment = adjustment
    line.adjustment_reason = (reason or "").strip()
    line.final_value = line.computed_value + adjustment
    if adjustment != Decimal("0"):
        line.adjusted_by = adjusted_by
        line.adjusted_at = timezone.now()
    else:
        line.adjusted_by = None
        line.adjusted_at = None
    line.save()  # DropLine.save() runs full_clean (reason required) + immutability guard
    return line


@transaction.atomic
def approve_run(run: DropRun, approved_by_membership=None) -> DropRun:
    """Transition a run open -> approved; its lines become issued + immutable.

    Line values are already final (set at open + each adjust), so approval only flips the
    run state and stamps who approved (``approved_by_membership``, the acting
    orgs.Membership) and when. After this, DropLine.save() refuses edits, so the lines are
    frozen equity that the Pie reads.
    """
    if run.is_approved:
        raise ValueError("Run is already approved.")
    run.state = DropRunState.APPROVED
    run.approved_by = approved_by_membership
    run.approved_at = timezone.now()
    run.save(update_fields=["state", "approved_by", "approved_at"])
    return run
