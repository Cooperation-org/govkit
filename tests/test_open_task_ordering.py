"""The dash's "tasks to do" card shows the tasks that need a person.

The card renders a handful of rows. Before ordering existed those were whatever
the tracker walked past first, which in practice was the oldest, deadest stories
on the board — the card read as stale however much the team actually did. These
tests pin the order.
"""

from datetime import date

from apps.tasksources.adapters import OpenTaskDTO
from apps.tasksources.ordering import most_important_first, rank

TODAY = date(2026, 8, 5)


def task(
    external_id="1",
    subject="a task",
    due_date=None,
    created_date="2026-01-10T09:00:00.000Z",
    modified_date="2026-06-10T09:00:00.000Z",
    assignee_label="sami",
    order=None,
):
    return OpenTaskDTO(
        external_id=external_id,
        subject=subject,
        due_date=due_date,
        created_date=created_date,
        modified_date=modified_date,
        assignee_label=assignee_label,
        order=order,
    )


def test_a_deadline_beats_anything_without_one():
    dated = rank(task(due_date="2027-12-31"), TODAY)
    undated = rank(task(created_date="2026-08-05T09:00:00.000Z"), TODAY)
    assert dated > undated


def test_the_soonest_deadline_comes_first():
    order = most_important_first(
        [
            task("1", "later", due_date="2026-09-01"),
            task("2", "sooner", due_date="2026-08-06"),
        ],
        TODAY,
    )
    assert [t.subject for t in order] == ["sooner", "later"]


def test_overdue_sits_above_everything():
    order = most_important_first(
        [
            task("1", "tomorrow", due_date="2026-08-06"),
            task("2", "missed", due_date="2026-07-01"),
        ],
        TODAY,
    )
    assert [t.subject for t in order] == ["missed", "tomorrow"]


def test_a_new_task_comes_before_a_stale_one():
    order = most_important_first(
        [
            task("1", "old", created_date="2024-01-01T09:00:00.000Z"),
            task("2", "new", created_date="2026-08-04T09:00:00.000Z"),
        ],
        TODAY,
    )
    assert [t.subject for t in order] == ["new", "old"]


def test_longer_untouched_comes_before_recently_touched():
    order = most_important_first(
        [
            task("1", "warm", modified_date="2026-08-01T09:00:00.000Z"),
            task("2", "cold", modified_date="2026-01-20T09:00:00.000Z"),
        ],
        TODAY,
    )
    assert [t.subject for t in order] == ["cold", "warm"]


def test_untouched_stops_counting_after_six_months():
    """Otherwise the oldest dead row on the board owns the card forever, which
    is the exact staleness this replaced."""
    six = rank(task(modified_date="2026-02-06T09:00:00.000Z"), TODAY)
    ancient = rank(task(modified_date="2019-02-06T09:00:00.000Z"), TODAY)
    assert ancient == six


def test_an_owned_task_comes_before_an_unowned_one():
    assert rank(task(), TODAY) > rank(task(assignee_label=None), TODAY)


def test_the_order_is_stable_between_two_identical_loads():
    """A card that reshuffles under the reader is worse than an imperfect one."""
    rows = [task("1", "beta"), task("2", "alpha")]
    assert [t.subject for t in most_important_first(rows, TODAY)] == [
        t.subject for t in most_important_first(list(reversed(rows)), TODAY)
    ]


def test_missing_dates_never_break_the_order():
    rows = [
        task("1", "no dates", created_date=None, modified_date=None),
        task("2", "bad date", due_date="not-a-date"),
    ]
    assert len(most_important_first(rows, TODAY)) == 2


def test_nothing_open_is_an_empty_list():
    assert most_important_first([], TODAY) == []


# --- an order a person put it in -------------------------------------------
# Golda 2026-08-17: "if is explicit respect it."


def test_an_arranged_task_beats_a_deadline():
    arranged = rank(task("1", order=3), TODAY)
    overdue = rank(task("2", due_date="2026-01-01"), TODAY)
    assert arranged > overdue


def test_arranged_tasks_come_back_in_the_order_they_were_put_in():
    order = most_important_first(
        [
            task("c", "third", order=3),
            task("a", "first", order=1),
            task("b", "second", order=2),
        ],
        TODAY,
    )
    assert [t.subject for t in order] == ["first", "second", "third"]


def test_arranging_some_leaves_the_rest_on_the_rule():
    order = most_important_first(
        [
            task("dead", "untouched for months", modified_date="2025-01-01T09:00:00.000Z"),
            task("due", "due soon", due_date="2026-08-06"),
            task("mine", "I put this here", order=1),
        ],
        TODAY,
    )
    assert [t.subject for t in order] == ["I put this here", "due soon", "untouched for months"]


def test_a_tracker_assigned_order_is_not_an_arrangement():
    """Taiga stamps backlog_order with microseconds at creation. That is the
    tracker numbering itself, not a person arranging anything."""
    stamped = task("1", order=1785141104630819, due_date=None)
    dated = task("2", due_date="2026-08-06")
    assert rank(dated, TODAY) > rank(stamped, TODAY)
