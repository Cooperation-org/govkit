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
        0,
        [
            ("exist.profile", "Complete your company profile"),
            (
                "exist.who-else",
                "Research the space: Who else is doing this?  Are there communities?  Consider asking them to partner",
            ),
            ("exist.invite", "Invite your team members"),
            ("exist.calendar", "Make a team calendar and add it in Settings"),
            (
                "exist.chat",
                "Find your team's channel in the LinkedTrust Discord and ensure all team members have access",
            ),
            ("exist.goals", "Create your first goals (example: find first customers)"),
            ("exist.tasks", "Create your first tasks on the task board (example: define customer archetype, reach out to friendlies, get feedback)"),
            ("exist.crm", "Create at least 3 initial contact targets on the CRM, maybe people you already know"),
        ],
    ),
    (
        "who",
        "Who's it for",
        1,
        [
            ("who.team-kickoff", "Hold your team kickoff meeting"),
            ("who.what-problem", "Write down what problem you are trying to solve, for who"),
            ("who.three-people", "Name three real people who have the problem"),
            ("who.talk-to-one", "Talk to one or more of them; write down what they said"),
            ("who.expand-crm", "Try to add 10 contacts to your CRM you want to reach out to"),
            ("who.present", "Present your results to the group in a 2 minute slot"),
            ("who.update-goals-tasks", "Update your goals and tasks.  Is there a demo or artifact you need to build to show people?"),
        ],
    ),
    (
        "build",
        "Build",
        2,
        [
            ("build.meeting", "Hold the Monday team meeting.  What can you build and share this week?"),
            ("build.smallest", "Define the smallest thing someone can try, or that you can show"),
            ("build.in-front-of-user", "Put it in front of one or more users"),
            ("build.research", "Research channels and potential targets.  How will you reach your customers?"),
            ("build.present", "Present your results to the group in a 2 minute slot"),
            ("build.update-goals-tasks", "Update your goals and tasks.  Approve completed work.  Has your archetype or core problem changed?"),
        ],
    ),
    (
        "iterate",
        "Iterate",
        3,
        [
            ("iterate.meeting", "Hold the Monday team meeting.  What do you want to iterate on based on feedback?"),
            ("iterate.smallest", "What is something you can actually try out this week"),
            ("iterate.in-front-of-user", "Put it in front of one or more users"),
            ("iterate.partners", "Talk to one or more potential partners, channels, or allies"),
            ("iterate.present", "Present your results to the group in a 2 minute slot"),
            ("iterate.update-goals-tasks", "Update your goals and tasks. Approve completed work. Will you seek direct customers, sales channels, and/or funding?"),
        ],
    ),
    (
        "money",
        "Money",
        4,
        [
            ("money.one-page", "One page: how money could come in"),
            ("money.first-yes", "Get a first yes at a real price"),
            ("money.funders", "If aiming for funding, send queries to seed funders"),
        ],
    ),
    (
        "agreements",
        "Agreements",
        5,
        [
            ("agreements.meeting", "Weekly team meeting - reflect on progress and team"),
            ("agreements.agreements", "Decide if you want to formalize an agreement through Slicing Pie or on the dashboard"),
            ("agreements.customize", "Review the standard agreements and decide if you want to customize them"),
            ("agreements.mutual", "Provide LinkedIn reviews, certificates and other mutual assistance to teammates"),
            ("agreements.plan", "Plan if and how to continue after the accelerator, what entity to form, and sign the customized agreements"),
        ],
    ),
]

# item key -> one line saying how to actually do it. Kept as its own dict rather
# than a third slot in the item tuples so that everything unpacking (key, title)
# keeps working. A missing brief is allowed and renders as nothing.
#
# AI-DRAFTED 2026-08-05 from golda's curriculum arc
# (~golda/work/7-15-2026-accelerator-curriculum-arc.md) and the reading page at
# workers.vc/curriculum/. Reword freely: titles and briefs are free to change,
# only the keys above are permanent.
ITEM_BRIEFS = {
    "exist.profile": "Two or three sentences: what it is, who it is for, why now. "
    "Plus one sentence, no adjectives, saying what you make. Your page in your own "
    "words. The link is how people join you.",
    "exist.who-else": "Partner with them, join them, or invite them in as co-owners. "
    "Here a competitor can become a contributor, because every share comes from work.",
    "exist.invite": "Everyone who will put hours in. Their work starts earning from "
    "their first approved task, so there is nothing to negotiate up front.",
    "exist.calendar": "Without a calendar your page cannot offer anyone a time to meet you.",
    "exist.chat": "Wherever your team already talks is fine. Tell us which one, so amebo "
    "can reach you there instead of somewhere you never look.",
    "who.three-people": "Real names, not a segment. If you cannot name three, you are "
    "guessing at who this is for.",
    "who.talk-to-one": "Ask about their problem, not your idea. Write down what they "
    "said in their words, not your summary of it.",
    "build.smallest": "The least you can put in front of someone to find out whether "
    "you are right. List its handful of features, not everything you want.",
    "build.in-front-of-user": "One real user, using it, while you watch. What they do "
    "beats what they say about it.",
    "money.one-page": "Who pays, for what, and how much. A guess with a number on it "
    "is testable. A guess without one is not.",
    "money.first-yes": "One person paying a real price tells you more than a room of "
    "people saying they like it.",
    "exist.tasks": "Your own board, your own key, in Settings. After this, hours on "
    "tasks become slices without anyone deciding anything.",
}

# item key -> [(label, where)] — the places a team has to go to actually do it.
# Some items are answered by typing; some are done somewhere else and the note is
# just the record. These are the "somewhere else" links, shown in the item's panel.
#
# `where` is either a path relative to this org (a leading "/" is filled in with
# /o/<slug>) or a full URL. Curriculum content, so it lives here with the items;
# a link for an item that no longer exists is dropped rather than raising, because
# a stale link is not worth breaking every dashboard over.
ITEM_LINKS = {
    "exist.profile": [("Your team page", "/settings/")],
    "exist.invite": [("Members", "/members/")],
    "exist.calendar": [("Settings", "/settings/")],
    "exist.chat": [("Settings", "/settings/")],
    "exist.tasks": [("Your task board", "/tasks/")],
    "money.one-page": [("Money", "/projects/")],
}

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
    # A brief for a key that no longer exists would silently never render.
    for item_key in ITEM_BRIEFS:
        if item_key not in seen:
            raise ValueError(f"brief for unknown curriculum item: {item_key}")


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

    __slots__ = ("key", "title", "brief", "links", "done", "done_at", "done_by", "retired")

    def __init__(self, key, title, event, retired=False):
        from .models import ChecklistAction

        done = event is not None and event.action == ChecklistAction.TICK
        self.key = key
        self.title = title
        # How to actually do it. Retired items have none, which renders as nothing.
        self.brief = "" if retired else ITEM_BRIEFS.get(key, "")
        # Where to go to do it, when the doing happens somewhere else.
        self.links = [] if retired else list(ITEM_LINKS.get(key, ()))
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


def serialize_modules(modules, org=None):
    """modules_for() output as JSON, for the dash embed and the cohort overview.

    `org` resolves each item's links to real URLs (they are written relative to
    the org in ITEM_LINKS) and says which items the team has already written
    something for. Without it the links are omitted rather than emitted
    half-built, so a caller that has no org in hand cannot ship a broken href.
    """
    written = _written_keys(org)
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
                    "brief": item.brief,
                    "links": _item_links(item, org),
                    "has_note": item.key in written,
                    "done": item.done,
                    "retired": item.retired,
                }
                for item in entry["items"]
            ],
        }
        for entry in modules
    ]


def _written_keys(org):
    """Item keys this org has a task for, i.e. has written something about."""
    if org is None:
        return frozenset()
    from .models import ChecklistTask

    return frozenset(
        ChecklistTask.objects.filter(org=org).values_list("item_key", flat=True)
    )


def _item_links(item, org):
    """[(label, where)] as [{label, url}], org-relative paths made absolute."""
    if org is None:
        return []
    base = f"/o/{org.slug}"
    out = []
    for label, where in item.links:
        out.append({"label": label, "url": where if "//" in where else base + where})
    return out


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
