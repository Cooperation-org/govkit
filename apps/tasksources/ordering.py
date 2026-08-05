"""Which open tasks go at the top of the dash's "tasks to do" card.

The card shows a handful of rows (``data-limit``, six by default). Until now the
open-task payload came back in whatever order the tracker walked its projects,
so those six were an arbitrary slice — in practice the oldest, deadest stories
on the board, which is why the card read as stale no matter what the team did.

The rule, the same one amebo's work list uses so a person does not meet two
different ideas of "important" in one week:

  1. Anything with a deadline comes first, soonest first. A date is a fact, not
     a judgement, and it needs no defending.
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


def rank(task: Any, today: Optional[date] = None) -> float:
    """How high this task sits. Higher is more urgent."""
    today = today or date.today()

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
