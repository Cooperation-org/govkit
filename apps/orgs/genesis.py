"""
Genesis checklist — the choose-your-own-adventure onboarding for a new venture org.

Five modules, per the accelerator curriculum arc (Golda's direction): a side index
with checkboxes, startable in ANY order. Week numbers are pace suggestions only;
nothing here enforces a sequence.

ITEM CONTENT IS PLACEHOLDER until Golda approves the curriculum arc draft
(~golda/work/7-15-2026-accelerator-curriculum-arc.md). Edit MODULES to change the
curriculum: every org renders from this list on the next request, including orgs
that started long ago.

JOIN, DON'T COPY (2026-07-22). The curriculum has ONE home: MODULES, below. Orgs
do not get a private copy of it. What an org stores is only what happened —
ChecklistEvent rows, append-only — and current state is derived by joining the
latest event per item against this list. Consequences, all deliberate:

  * Curriculum edits reach every org immediately. There is nothing to reseed.
  * Unchecking appends an event instead of destroying one, so the record of
    having done something survives, and "which item does every team skip" is a
    query rather than lost information.
  * An org is "on the path" because Org.genesis_started_at is set, never because
    rows happen to exist.

Item keys (the first element of each item tuple) are the join key and are
PERMANENT. Rewording an item is free; changing its key orphans its history.
Prefix must match the module key.
"""

from __future__ import annotations

# (module key, label, suggested week, [(item key, title), ...])
#
# Week is explicit, not derived from position: inserting a module must not
# silently renumber the weeks every other team is looking at.
MODULES = [
    (
        "exist",
        "Exist",
        1,
        [
            ("exist.prior-art", "Prior art: find who already does this, before building anything"),
            ("exist.paragraph", "Write one plain paragraph: what this is and why now"),
            ("exist.who-else", "List who is working on this today"),
        ],
    ),
    (
        "who",
        "Who's it for",
        2,
        [
            ("who.three-people", "Name three real people who have the problem"),
            ("who.talk-to-one", "Talk to one of them; write down what they said"),
        ],
    ),
    (
        "build",
        "Build",
        3,
        [
            ("build.smallest", "Define the smallest thing someone can try"),
            ("build.in-front-of-user", "Put it in front of one user"),
        ],
    ),
    (
        "money",
        "Money",
        4,
        [
            ("money.one-page", "One page: how money could come in"),
            ("money.first-yes", "Get a first yes at a real price"),
        ],
    ),
    (
        "receipts",
        "Receipts",
        5,
        [
            ("receipts.connect-tracker", "Connect your task tracker so reviewed work earns slices"),
            ("receipts.first-drop", "Run your first drop: review and approve the week's work"),
            ("receipts.trace-a-slice", "Open your pie and trace a slice back to the work"),
        ],
    ),
]

MODULE_LABELS = {key: label for key, label, _week, _items in MODULES}
MODULE_WEEKS = {key: week for key, _label, week, _items in MODULES}

# item key -> (module key, title), the flat index the derive step joins against.
ITEM_INDEX = {
    item_key: (module_key, title)
    for module_key, _label, _week, items in MODULES
    for item_key, title in items
}


def _check_keys():
    """Keys are the join key: unique, and prefixed with their own module."""
    seen = set()
    for module_key, _label, _week, items in MODULES:
        for item_key, _title in items:
            if item_key in seen:
                raise ValueError(f"duplicate curriculum item key: {item_key}")
            seen.add(item_key)
            if not item_key.startswith(f"{module_key}."):
                raise ValueError(f"item key {item_key!r} must start with {module_key!r}.")


_check_keys()


def module_of(item_key):
    """The module an item belongs to, including retired items (prefix is the key)."""
    known = ITEM_INDEX.get(item_key)
    return known[0] if known else item_key.split(".", 1)[0]


def start_genesis(org):
    """Put an org on the path. Idempotent; keeps the original start date."""
    from django.utils import timezone

    if org.genesis_started_at is None:
        org.genesis_started_at = timezone.now()
        org.save(update_fields=["genesis_started_at"])


def toggle_item(org, item_key, actor):
    """
    Flip one item by appending the opposite event. Returns (done, module entry),
    or (None, None) when the key is not part of the current curriculum.

    Nothing is updated or deleted: an untick is a new row. Two members racing on
    the same item both get recorded, and the later one wins the derived state.
    """
    from .models import ChecklistAction, ChecklistEvent

    known = ITEM_INDEX.get(item_key)
    if known is None or org.genesis_started_at is None:
        return None, None
    _module_key, title = known

    current = latest_events(org).get(item_key)
    done_now = current is not None and current.action == ChecklistAction.TICK
    ChecklistEvent.objects.create(
        org=org,
        item_key=item_key,
        action=ChecklistAction.UNTICK if done_now else ChecklistAction.TICK,
        actor=actor if actor and actor.is_authenticated else None,
        title_shown=title,
    )
    entry = next((e for e in modules_for(org) if e["key"] == module_of(item_key)), None)
    return (not done_now), entry


def latest_events(org):
    """The current event per item key for one org: {item_key: ChecklistEvent}."""
    from .models import ChecklistEvent

    latest = {}
    for event in (
        ChecklistEvent.objects.filter(org=org).select_related("actor").order_by("at", "id")
    ):
        latest[event.item_key] = event
    return latest


class _Item:
    """One rendered checklist item. Derived per request; never stored."""

    __slots__ = ("key", "title", "done", "done_at", "done_by", "retired")

    def __init__(self, key, title, event, retired=False):
        from .models import ChecklistAction

        done = event is not None and event.action == ChecklistAction.TICK
        self.key = key
        self.title = title
        self.done = done
        self.done_at = event.at if done else None
        self.done_by = event.actor if done else None
        # True when the curriculum has since dropped this item. It still renders
        # if a team ticked it, because their record of doing it is theirs.
        self.retired = retired


def modules_for(org):
    """
    The org's curriculum as modules of items, in MODULES order, with done counts —
    the shape the dashboard side index renders. Empty list = not on the path.

    Current state is derived here: live MODULES joined with the org's latest
    event per item. Retired items that were ticked trail their module, titled as
    they read at the time.
    """
    if org.genesis_started_at is None:
        return []

    latest = latest_events(org)
    orphans = {k: e for k, e in latest.items() if k not in ITEM_INDEX}

    modules = []
    for module_key, label, week, items in MODULES:
        rendered = [_Item(key, title, latest.get(key)) for key, title in items]
        rendered += _retired_items(orphans, module_key)
        modules.append(_module_entry(module_key, label, week, rendered))

    # A whole module dropped from the curriculum: its ticked items still show,
    # after the live ones, so nobody's record silently disappears.
    for module_key in dict.fromkeys(module_of(k) for k in orphans):
        rendered = _retired_items(orphans, module_key)
        if rendered:
            modules.append(_module_entry(module_key, module_key.title(), None, rendered))
    return modules


def serialize_modules(modules):
    """modules_for() output as JSON, for the dash embed and the cohort overview."""
    return [
        {
            "key": entry["key"],
            "title": entry["label"],
            "week": entry["week"],
            "done": entry["done"],
            "total": entry["total"],
            "items": [
                {
                    "key": item.key,
                    "title": item.title,
                    "done": item.done,
                    "retired": item.retired,
                }
                for item in entry["items"]
            ],
        }
        for entry in modules
    ]


def _retired_items(orphans, module_key):
    from .models import ChecklistAction

    return [
        _Item(key, event.title_shown, event, retired=True)
        for key, event in sorted(orphans.items())
        if module_of(key) == module_key and event.action == ChecklistAction.TICK
    ]


def _module_entry(key, label, week, items):
    return {
        "key": key,
        "label": label,
        # None for modules no longer part of MODULES (content edits).
        "week": week,
        "items": items,
        "done": sum(1 for i in items if i.done),
        "total": len(items),
    }
