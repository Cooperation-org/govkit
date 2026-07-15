"""
Genesis checklist — the choose-your-own-adventure onboarding for a new venture org.

Five modules, per the accelerator curriculum arc (Golda's direction): a side index
with checkboxes, startable in ANY order. Week numbers in the curriculum are pace
suggestions only; nothing here enforces a sequence.

ITEM CONTENT IS PLACEHOLDER until Golda approves the curriculum arc draft
(~golda/work/7-15-2026-accelerator-curriculum-arc.md). The seeding MECHANISM is the
deliverable (build brief B4); edit MODULES to change content — reseeding only affects
orgs created after the edit.
"""

from __future__ import annotations

# (key, label, [item titles]) — module order here is the side-index order.
MODULES = [
    (
        "exist",
        "Exist",
        [
            "Prior art: find who already does this, before building anything",
            "Write one plain paragraph: what this is and why now",
            "List who is working on this today",
        ],
    ),
    (
        "who",
        "Who's it for",
        [
            "Name three real people who have the problem",
            "Talk to one of them; write down what they said",
        ],
    ),
    (
        "build",
        "Build",
        [
            "Define the smallest thing someone can try",
            "Put it in front of one user",
        ],
    ),
    (
        "money",
        "Money",
        [
            "One page: how money could come in",
            "Get a first yes at a real price",
        ],
    ),
    (
        "receipts",
        "Receipts",
        [
            "Connect your task tracker so reviewed work earns slices",
            "Run your first drop: review and approve the week's work",
            "Open your pie and trace a slice back to the work",
        ],
    ),
]

MODULE_LABELS = {key: label for key, label, _ in MODULES}


def seed_genesis(org):
    """Load the module checklist into a freshly created venture org."""
    from .models import ChecklistItem

    items = []
    for key, _label, titles in MODULES:
        for i, title in enumerate(titles):
            items.append(ChecklistItem(org=org, module=key, title=title, order=i))
    ChecklistItem.objects.bulk_create(items)


def modules_for(org):
    """
    Group an org's checklist items by module, in MODULES order, with done counts —
    the shape the dashboard's side index renders. Empty list = not a venture org.
    """
    from .models import ChecklistItem

    by_module = {}
    for item in ChecklistItem.objects.filter(org=org).order_by("order", "id"):
        by_module.setdefault(item.module, []).append(item)

    modules = []
    for key, label, _titles in MODULES:
        items = by_module.pop(key, [])
        if items:
            modules.append(_module_entry(key, label, items))
    # Modules no longer in MODULES (content edits) still render, after the known ones.
    for key, items in by_module.items():
        modules.append(_module_entry(key, key.title(), items))
    return modules


def _module_entry(key, label, items):
    done = sum(1 for i in items if i.done_at)
    return {"key": key, "label": label, "items": items, "done": done, "total": len(items)}
