"""Which open tasks go at the top of the dash's "tasks to do" card.

The card shows a handful of rows (``data-limit``, six by default). Until now the
open-task payload came back in whatever order the tracker walked its projects,
so those six were an arbitrary slice — in practice the oldest, deadest stories
on the board, which is why the card read as stale no matter what the team did.

The rule, the same one amebo's work list uses so a person does not meet two
different ideas of "important" in one week:

  0. A task the team ARRANGED BY HAND comes first, in the order they put it in.
     Golda 2026-08-17: "if is explicit respect it." Somebody dragging a row
     knows something no rule here can work out, and a list that quietly
     reshuffles itself afterwards is a list nobody will touch twice.
  1. Then anything with a deadline, soonest first. A date is a fact, not a
     judgement, and it needs no defending.
  2. Everything else is ordered by whether it needs a look: brand new (nobody
     has triaged it yet), then longest untouched, because a task nobody has
     touched in months is being ignored rather than handled.

Six months is where "untouched" stops meaning more, so the oldest dead row on
the board cannot own the top of the card forever.

Ordering only. Nothing here decides what is open, and nothing is persisted.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional, Sequence

# An arranged task outranks every rule. Its position is the tracker's own order
# field, written as 1, 2, 3... by whoever dragged it (adapters' set_order).
# Taiga numbers a story it created with a microsecond stamp, ~1.8e15, so a
# number under this ceiling can only have been put there by a person arranging
# the list. Nothing else in the band is possible.
EXPLICIT_CEILING = 1_000_000
EXPLICIT_FLOOR = 10000.0

# A dated task always outranks an undated one, the way a deadline outranks a
# hunch. The bands never overlap.
CLOCK_FLOOR = 1000.0
UNDATED_FLOOR = 300.0
UNDATED_NEW_DAYS = 5  # under this, nobody has looked at it yet
UNDATED_NEW = 250.0
UNDATED_OWNED = 60.0  # someone owns it -> it can actually move
STALE_CAP = 180.0  # past six months untouched, older means no more


def _day(stamp: Optional[str]) -> Optional[date]:
    """The date half of a tracker timestamp. Taiga sends both
    '2026-08-13' and '2023-01-03T23:27:05.322Z'."""
    if not stamp:
        return None
    try:
        return date.fromisoformat(str(stamp)[:10])
    except ValueError:
        return None


def _days_since(stamp: Optional[str], today: date) -> Optional[int]:
    d = _day(stamp)
    return None if d is None else max(0, (today - d).days)


def placed_at(task: Any) -> Optional[int]:
    """The position a person put this task in, or None if nobody has."""
    order = getattr(task, "order", None)
    if isinstance(order, int) and 0 < order < EXPLICIT_CEILING:
        return order
    return None


def rank(task: Any, today: Optional[date] = None) -> float:
    """How high this task sits. Higher is more urgent."""
    today = today or date.today()

    placed = placed_at(task)
    if placed is not None:
        # First arranged is highest, and every arranged task sits above every
        # task no one has arranged — including anything overdue. That is what
        # respecting an explicit order means.
        return EXPLICIT_FLOOR + max(0.0, 10000.0 - placed)

    due = _day(getattr(task, "due_date", None))
    if due is not None:
        # Sooner is higher, and overdue is highest of all.
        return CLOCK_FLOOR + max(0.0, 365.0 - (due - today).days)

    score = UNDATED_FLOOR
    if getattr(task, "assignee_label", None):
        score += UNDATED_OWNED

    age = _days_since(getattr(task, "created_date", None), today)
    if age is not None and age <= UNDATED_NEW_DAYS:
        score += UNDATED_NEW
    else:
        quiet = _days_since(getattr(task, "modified_date", None), today)
        if quiet is not None:
            score += min(float(quiet), STALE_CAP)
    return score


def most_important_first(tasks: Sequence[Any], today: Optional[date] = None) -> list:
    """The open tasks in the order a person should see them.

    Ties break on the subject so the card does not reshuffle between two loads
    of identical data — a list that moves under the reader is worse than one in
    a merely imperfect order.
    """
    today = today or date.today()
    return sorted(tasks, key=lambda t: (-rank(t, today), getattr(t, "subject", "") or ""))
