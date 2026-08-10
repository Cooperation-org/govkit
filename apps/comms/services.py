"""
Building a week's email, and reading it back after a person has edited it.

Nothing here writes prose. A calendar line carries the facts the calendar already
has — what, when, where, the link to the event — and an empty note for the
sentence saying why this audience should turn up. That sentence is a person's, or
an AI's when a person asks for one (rewrite.py). It is never invented on the way
in.

The email a person edits is a contenteditable list, so the way it is saved is:
read the lines back, match each to the line it came from by id, and treat a line
that is no longer there as cut from this audience only. Cutting is deleting; the
cut line comes back as a chip outside the border.
"""

from __future__ import annotations

import re
import secrets
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.utils.html import escape
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import calendar as calendar_feed
from .models import (
    AUDIENCE_KEYS,
    MENTORS,
    VENTURES,
    WORKERS,
    DEFAULT_SECTIONS,
    SUPPORTERS,
    Edition,
    Send,
    Subscriber,
)
from .sources import crm, govkit

# The email is about the week ahead. It is written and sent in the days before
# that week starts, so "the current edition" is the week that has not begun yet.
CALENDAR_SECTION = "cal"
LEAD_DAYS = 3
# How far ahead the email looks. It goes out weekly and is named for its own
# week, but it lists the fortnight: people book travel and childcare further
# out than seven days, and a line that is too early is one press to cut
# (golda 2026-08-10).
WEEKS_AHEAD = 2


def week_window(today: date | None = None) -> tuple[date, date]:
    """(first Monday, last day) of the stretch the next email is about."""
    today = today or timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    if (monday + timedelta(days=7) - today).days <= LEAD_DAYS + 1:
        monday += timedelta(days=7)
    elif today.weekday() >= 4:
        monday += timedelta(days=7)
    return monday, monday + timedelta(days=7 * WEEKS_AHEAD - 1)


def week_number(org_slug: str, window_start: date):
    started = govkit.cohort_start(org_slug)
    if not started:
        return None
    weeks = (window_start - (started - timedelta(days=started.weekday()))).days // 7
    return weeks + 1 if weeks >= 0 else None


def open_edition(org_slug: str, today: date | None = None) -> Edition:
    """The week ahead, with the calendar already read into it.

    Called on every visit. It builds the edition the first time and then leaves
    it alone: once a person has edited an email, re-reading the calendar must not
    overwrite what they wrote. Meetings added to the calendar afterwards show up
    under "not in this" for them to put in.
    """
    start, end = week_window(today)
    existing = Edition.objects.filter(org_slug=org_slug, window_start=start).first()
    if existing is not None:
        _catch_up(existing, end)
        return existing

    events, tz_name, _problem = read_calendar(org_slug, start, end)
    edition = Edition(
        org_slug=org_slug,
        week_number=week_number(org_slug, start),
        window_start=start,
        window_end=end,
        tz_name=tz_name,
        sections=[dict(s) for s in DEFAULT_SECTIONS],
        items=[],
    )
    edition.items = [item_from_event(edition, e) for e in events]
    edition.items += _carried_forward(org_slug, edition)
    _seed_section(edition, GOALS_SECTION, _goal_lines(edition))
    _seed_section(edition, OPPORTUNITIES_SECTION, _opportunity_lines(edition))
    _seed_section(edition, VENTURES_SECTION, _venture_news_lines(edition))
    try:
        with transaction.atomic():
            edition.save()
    except IntegrityError:
        # Two tabs opened the same week at once; the first one wins.
        edition = Edition.objects.get(org_slug=org_slug, window_start=start)
    return edition


def _catch_up(edition: Edition, end: date) -> None:
    """Bring a draft up to the stretch it should be showing.

    Two ways it can fall short. It was built while the calendar was unreachable
    and holds no meeting at all, or the stretch itself moved out (one week to
    two). Neither is an overwrite: those meetings were never offered to anybody.

    A cut line keeps its uid in `items`, so nothing here puts back what someone
    took out, and a meeting already in the email is left exactly as it is.
    """
    seeded = _seed_section(edition, GOALS_SECTION, _goal_lines(edition))
    seeded |= _seed_section(edition, OPPORTUNITIES_SECTION, _opportunity_lines(edition))
    seeded |= _seed_section(edition, VENTURES_SECTION, _venture_news_lines(edition))
    widened = end > edition.window_end
    empty = not any(i.get("sec") == CALENDAR_SECTION for i in edition.items)
    if not (widened or empty):
        if seeded:
            edition.save(update_fields=["items", "updated_at"])
        return
    if widened:
        edition.window_end = end
    events, tz_name, _problem = read_calendar(edition.org_slug, edition.window_start, end)
    if tz_name and not edition.tz_name:
        # Set before the items are built: their day and time read in this zone.
        edition.tz_name = tz_name
    known = {i.get("uid") for i in edition.items if i.get("uid")}
    fresh = [item_from_event(edition, e) for e in events if e.uid not in known]
    if not (fresh or widened or seeded):
        return
    edition.items += fresh
    _sort_calendar(edition)
    edition.save(update_fields=["items", "window_end", "tz_name", "updated_at"])


GOALS_SECTION = "goals"
OPPORTUNITIES_SECTION = "opp"
VENTURES_SECTION = "vent"


def sent_audiences(edition: Edition) -> set:
    """The audiences whose email has already gone. Theirs is now a record."""
    if edition.pk is None:
        # Still being built, so nothing has been sent from it yet.
        return set()
    return set(edition.sends.filter(sent_at__isnull=False).values_list("audience", flat=True))


def _seed_section(edition: Edition, key: str, lines: list[dict]) -> bool:
    """Put lines into a section that has none FOR THE AUDIENCE THEY ARE FOR.

    Same rule as the calendar, but read per audience, because a section holds
    a different set of lines for each one: the goals section can be full for
    the workers and empty for the ventures, and the ventures still need theirs
    (golda 2026-08-10). Once an audience has a line here — even one that has
    been cut, which stays in `items` carrying `off` — this leaves it alone, so
    a line somebody deleted does not come back next time the page loads.
    """
    # Read once, before anything is added: otherwise the first line seeded for
    # an audience is itself the reason the second one is skipped. An audience
    # whose email has gone counts as done: what was sent is the record of what
    # was sent, and nothing may appear in it afterwards (golda 2026-08-10).
    already = sent_audiences(edition) | {
        audience
        for i in edition.items
        if i.get("sec") == key
        for audience in (i.get("tpl") or AUDIENCE_KEYS)
    }
    landed = False
    for line in lines:
        if already.intersection(line.get("tpl") or AUDIENCE_KEYS):
            continue
        item = blank_item(edition, key)
        item.update(line)
        edition.items.append(item)
        landed = True
    return landed


def _goal_lines(edition: Edition) -> list[dict]:
    """This week's goals. Each audience gets its own, so each line says whose.

    A goal for a worker is not a goal for a mentor (golda 2026-08-10), and a
    line carries the audiences it goes to, so they live side by side rather
    than needing a section each.
    """
    lines = [
        {"title": title, "href": url, "tpl": [WORKERS]} for title, url in govkit.worker_goals()
    ]
    lines += [
        {"title": title, "href": url, "tpl": [VENTURES]} for title, url in govkit.venture_goals()
    ]
    return lines


def _people_line(people: list[dict], plural: str, url: str) -> list[dict]:
    """New arrivals as one line: their names, and where the rest of them are.

    Naming each one on its own line does not survive a busy week — you cannot
    list everybody (golda 2026-08-10). The link is to the page that holds all
    of them rather than to any one person.
    """
    if not people:
        return []
    names = ", ".join(person["name"] for person in people)
    return [{"title": f"{names} joined as {plural}", "href": url, "flag": "new", "tpl": [VENTURES]}]


def _venture_news_lines(edition: Edition) -> list[dict]:
    """What the mentors are told about the teams.

    Every team, not only the ones that just arrived: a mentor is reading this
    to know who is in the run, and a team that joined three weeks ago is still
    who they might sit with (golda 2026-08-10). The recent ones are marked.

    What a team has actually DONE this week is not derivable from anything we
    hold, so the section is seeded with the teams and the highlights are
    written in.
    """
    since = edition.window_start - timedelta(days=7)
    return [
        {
            "title": v["name"],
            "href": v["url"],
            "note": v["note"],
            "flag": "new" if v["is_new"] else "",
            "tpl": [MENTORS],
        }
        for v in govkit.ventures(edition.org_slug, since)
    ]


def _opportunity_lines(edition: Edition) -> list[dict]:
    """Who and what turned up in the week before this email is about.

    A worker in the pool is reading this for the teams that just arrived. A
    team is reading it for the people — the mentors by name, and the workers
    by name until there are too many to read, and then by how many.
    """
    since = edition.window_start - timedelta(days=7)
    org_slug = edition.org_slug
    lines = [
        {
            "title": v["name"],
            "href": v["url"],
            "note": v["note"],
            "flag": "new",
            "tpl": [WORKERS],
        }
        for v in govkit.new_ventures(org_slug, since)
    ]
    # Each list points at the page that holds all of those people.
    where = (
        (MENTORS, "mentors", govkit.mentors_url()),
        (WORKERS, "workers", govkit.pool_url()),
    )
    for audience, plural, url in where:
        lines += _people_line(govkit.new_people(org_slug, since, audience), plural, url)
    return lines


def reread_calendar(edition: Edition, overwrite: bool = False) -> None:
    """Take the calendar's word for the meetings that are already in the email.

    The calendar owns when a meeting is and what it is called; comms owns the
    sentence a human wrote about it and whether it is in this audience's email
    (module docstring in calendar.py). So a re-read updates day, time and title
    on the lines it can match by uid, and touches nothing else — the note, the
    cuts and the person's own lines all stand. A field somebody has edited is
    theirs from then on and the calendar stops speaking for it, unless
    `overwrite` says to take the calendar's word back — for when the edit is
    the thing that is now wrong.

    A meeting that has left the calendar keeps its line. Dropping someone's row
    out from under them is worse than a stale one they can cut.
    """
    calendar_feed.forget(govkit.calendar_url(edition.org_slug))
    events, tz_name, _problem = read_calendar(
        edition.org_slug, edition.window_start, edition.window_end
    )
    if not events:
        return
    if tz_name and tz_name != edition.tz_name:
        edition.tz_name = tz_name
    by_uid = {e.uid: e for e in events}
    for item in edition.items:
        event = by_uid.get(item.get("uid"))
        if event is not None:
            facts = event_facts(edition, event)
            if overwrite:
                item.update(facts)
                item["cal"] = facts
            else:
                take_calendars_word(item, facts)
    _sort_calendar(edition)
    edition.save(update_fields=["items", "tz_name", "updated_at"])


def _carried_forward(org_slug: str, edition: Edition) -> list[dict]:
    """The standing sections, copied from last week so they are not retyped.

    A footer that says how to follow us and how to sponsor is the same every
    week until someone changes it, and changing it should hold. Copying the
    lines forward means this week's edition owns its own copy: editing it does
    not rewrite an email that already went.
    """
    carried = [s["k"] for s in edition.sections if s.get("carry")]
    if not carried:
        return []
    previous = (
        Edition.objects.filter(org_slug=org_slug, window_start__lt=edition.window_start)
        .order_by("-window_start")
        .first()
    )
    if previous is None:
        return []
    out = []
    for item in previous.items:
        if item.get("sec") in carried:
            out.append({**item, "id": secrets.token_hex(4), "off": list(item.get("off") or [])})
    return out


def read_calendar(org_slug: str, start: date, end: date):
    return calendar_feed.read(govkit.calendar_url(org_slug), start, end)


# --- the audiences -----------------------------------------------------------


def send_for(edition: Edition, audience: str) -> Send:
    """This audience's copy. Created on first look, subject already written."""
    send, _made = Send.objects.get_or_create(
        edition=edition,
        audience=audience,
        defaults={"subject": default_subject(edition, audience)},
    )
    return send


def default_subject(edition: Edition, audience: str = "") -> str:
    """Supporters are not in the run, so a week number means nothing to them."""
    if audience == SUPPORTERS:
        return f"News from {govkit.display_name(edition.org_slug)}"
    if edition.week_number:
        return f"Welcome to week {edition.week_number}"
    return f"Week of {edition.window_start:%b %-d}"


def audience_size(org_slug: str, audience: str):
    """How many people one email would go to, or None when it is not knowable.

    None renders as nothing. A made-up recipient count on a screen whose next
    button sends email is worse than no number at all.
    """
    if audience == SUPPORTERS:
        return subscribers(org_slug, audience).count()
    return govkit.audience_size(org_slug, audience)


def audience_state(edition: Edition, org_slug: str) -> list[dict]:
    """The tab row: each audience, and where its email has got to.

    Status is a word in a tint, never an icon: a count while it is still being
    written, the date once it is scheduled, "sent" once it has gone.
    """
    from .models import AUDIENCES

    sends = {s.audience: s for s in edition.sends.all()}
    out = []
    for key, label in AUDIENCES:
        send = sends.get(key)
        out.append(
            {
                "key": key,
                "label": label,
                "size": audience_size(org_slug, key),
                "sent": bool(send and send.is_sent),
                "scheduled_for": send.scheduled_for if send else None,
                "sent_at": send.sent_at if send else None,
            }
        )
    return out


# --- reading the email -------------------------------------------------------


def belongs(edition: Edition, item: dict, audience: str) -> bool:
    section = edition.section(item.get("sec", ""))
    allowed = item.get("tpl") or (section or {}).get("tpl") or AUDIENCE_KEYS
    return audience in allowed


def email(edition: Edition, audience: str) -> list[dict]:
    """The sections this audience gets, each with the lines still in its email."""
    out = []
    for section in edition.sections:
        if audience not in section.get("tpl", AUDIENCE_KEYS):
            continue
        rows = [
            _row(item, section)
            for item in edition.items
            if item.get("sec") == section["k"]
            and belongs(edition, item, audience)
            and audience not in item.get("off", [])
        ]
        if rows or section.get("title"):
            out.append({**section, "rows": rows})
    return out


def cut_items(edition: Edition, audience: str) -> list[dict]:
    """What this audience's email had and no longer does — the chips."""
    return [
        _row(i, edition.section(i.get("sec", "")) or {})
        for i in edition.items
        if belongs(edition, i, audience) and audience in i.get("off", [])
    ]


def _row(item: dict, section: dict) -> dict:
    is_calendar = bool(section.get("cal"))
    return {
        **item,
        "is_calendar": is_calendar,
        "optional": is_calendar and not item.get("rec"),
    }


# --- writing it back ---------------------------------------------------------


def save_section(edition: Edition, audience: str, sec_key: str, rows: list[dict]) -> bool:
    """Take the email back after a person typed in it.

    `rows` is what the browser read out of one section: `id` for a line that was
    already there, no id for one the person wrote. A line that is gone from the
    list was deleted, which is how this design cuts things — so it is marked off
    for THIS audience and stays in the others.

    Returns True when the set of lines changed, so the page knows to redraw: a
    new line has to pick up its id, and a cut one has to appear as a chip.
    """
    section = edition.section(sec_key)
    if section is None:
        return False
    by_id = {i.get("id"): i for i in edition.items}
    kept: set[str] = set()
    written: list[dict] = []
    structural = False

    for row in rows:
        title = (row.get("title") or "").strip()
        if not title:
            continue
        item = by_id.get(row.get("id"))
        if item is None:
            item = blank_item(edition, sec_key)
            edition.items.append(item)
            structural = True
        item["title"] = title
        item["note"] = (row.get("note") or "").strip()
        kept.add(item["id"])
        written.append(item)

    for item in edition.items:
        if item.get("sec") != sec_key or not belongs(edition, item, audience):
            continue
        off = list(item.get("off") or [])
        was_in = audience not in off
        if item.get("id") in kept:
            off = [a for a in off if a != audience]
        elif audience not in off:
            off.append(audience)
        if was_in != (audience not in off):
            structural = True
        item["off"] = off

    _reorder(edition, sec_key, [i["id"] for i in written])
    edition.save(update_fields=["items", "updated_at"])
    return structural


def _reorder(edition: Edition, sec_key: str, order: list[str]) -> None:
    """Put a section's lines in the order the person left them in."""
    rank = {item_id: n for n, item_id in enumerate(order)}
    section_items = [i for i in edition.items if i.get("sec") == sec_key]
    section_items.sort(key=lambda i: rank.get(i.get("id"), len(rank)))
    rest = [i for i in edition.items if i.get("sec") != sec_key]
    at = next((n for n, i in enumerate(edition.items) if i.get("sec") == sec_key), len(rest))
    edition.items = rest[:at] + section_items + rest[at:]


def set_section_title(edition: Edition, sec_key: str, title: str) -> None:
    section = edition.section(sec_key)
    if section is None:
        return
    section["title"] = title.strip()
    edition.save(update_fields=["sections", "updated_at"])


def restore_item(edition: Edition, audience: str, item_id: str) -> None:
    """Press a chip: the line goes back into this audience's email."""
    item = edition.item(item_id)
    if item is None:
        return
    item["off"] = [a for a in (item.get("off") or []) if a != audience]
    edition.save(update_fields=["items", "updated_at"])


def add_event(edition: Edition, uid: str) -> None:
    """Put a meeting that appeared on the calendar after the week was built in."""
    events, _tz, _problem = read_calendar(
        edition.org_slug, edition.window_start, edition.window_end
    )
    event = next((e for e in events if e.uid == uid), None)
    if event is None:
        return
    edition.items.append(item_from_event(edition, event))
    _sort_calendar(edition)
    edition.save(update_fields=["items", "updated_at"])


def missing_events(edition: Edition) -> tuple[list, str]:
    """(what is on the calendar and in no email, problem)."""
    events, _tz, problem = read_calendar(edition.org_slug, edition.window_start, edition.window_end)
    taken = {i.get("uid") for i in edition.items if i.get("uid")}
    return [e for e in events if e.uid not in taken], problem


def _sort_calendar(edition: Edition) -> None:
    cal = [i for i in edition.items if i.get("sec") == CALENDAR_SECTION]
    cal.sort(key=lambda i: i.get("starts") or "")
    before = [i for i in edition.items if i.get("sec") != CALENDAR_SECTION]
    at = next(
        (n for n, i in enumerate(edition.items) if i.get("sec") == CALENDAR_SECTION),
        len(before),
    )
    edition.items = before[:at] + cal + before[at:]


# --- lines -------------------------------------------------------------------


def blank_item(edition: Edition, sec_key: str) -> dict:
    section = edition.section(sec_key) or {}
    return {
        "id": secrets.token_hex(4),
        "sec": sec_key,
        "uid": "",
        "starts": "",
        "day": "",
        "time": "",
        "title": "",
        "note": "",
        "flag": "",
        "rec": True,
        "href": "",
        "tpl": list(section.get("tpl", AUDIENCE_KEYS)),
        "off": [],
    }


_SUFFIX = {1: "st", 2: "nd", 3: "rd"}


def _ordinal(day: int) -> str:
    if 11 <= day <= 13:
        return f"{day}th"
    return f"{day}{_SUFFIX.get(day % 10, 'th')}"


def event_facts(edition: Edition, event) -> dict:
    """What the CALENDAR says about a meeting: when it is, and what it is called.

    The date carries its month, and the time carries its zone. The email looks
    a fortnight ahead and goes to people in Phoenix, Lagos and Berlin at once,
    so a bare "11" and a bare "8:00am" are both a meeting somebody misses.
    """
    local = event.starts.astimezone(zone(edition))
    return {
        "starts": event.starts.isoformat(),
        "day": f"{local:%a %b} {_ordinal(local.day)}",
        "time": (
            ""
            if event.all_day
            else (local.strftime("%-I:%M%p").lower() + " " + local.strftime("%Z")).strip()
        ),
        "title": event.title,
        # What a person clicks a meeting for is the way in: its Meet or Zoom
        # link. Only a meeting that has none falls back to the calendar.
        "href": event.url or govkit.calendar_url(edition.org_slug),
    }


def item_from_event(edition: Edition, event) -> dict:
    item = blank_item(edition, CALENDAR_SECTION)
    facts = event_facts(edition, event)
    item.update(facts)
    # What the calendar last said, kept beside what the line now says. A
    # re-read compares the two and only replaces a field nobody has touched.
    item.update({"uid": event.uid, "note": event.where, "rec": True, "cal": facts})
    return item


def take_calendars_word(item: dict, facts: dict) -> None:
    """Update the calendar's fields on one line, keeping anything edited.

    A line whose title still reads exactly what the calendar last said is the
    calendar's to change. One a person rewrote is theirs, and a button called
    Read it again must not be the thing that loses it.

    A line drafted before we started recording this has no `cal` to compare
    against, so it is left alone. It can be cut and put back to get the new
    wording, which is a choice rather than a surprise.
    """
    was = item.get("cal") or {}
    for field, value in facts.items():
        if was.get(field) == item.get(field):
            item[field] = value
    item["cal"] = facts


def zone(edition: Edition):
    for name in (edition.tz_name, settings.TIME_ZONE):
        if name:
            try:
                return ZoneInfo(name)
            except ZoneInfoNotFoundError:
                continue
    return timezone.get_default_timezone()


# --- the mailing list --------------------------------------------------------


def subscribers(org_slug: str, audience: str):
    """Everyone on one list who has not unsubscribed."""
    return Subscriber.objects.filter(
        org_slug=org_slug, audience=audience, unsubscribed_at__isnull=True
    )


def recipient_emails(org_slug: str, audience: str) -> list[str]:
    """The addresses this email would go to, for pasting into a mail client.

    Supporters is a list comms builds up itself. The cohort audiences are
    GovKit's: the invite is the join record, and it carries both the address
    and which door the person came through. Either way an unsubscribe wins.
    """
    if audience == SUPPORTERS:
        found = subscribers(org_slug, audience).values_list("email", flat=True)
    else:
        found = govkit.audience_emails(org_slug, audience)
    gone = set(
        Subscriber.objects.filter(org_slug=org_slug, unsubscribed_at__isnull=False).values_list(
            "email", flat=True
        )
    )
    # One unsubscribe covers every list and every source (models.Subscriber),
    # so it has to hold for a list GovKit hands over too.
    return sorted({e.strip() for e in found if e and e.strip() not in gone})


def import_from_crm(org_slug: str, audience: str, tag_id: int, tag_name: str):
    """Bring a CRM tag onto a list. Returns (added, refreshed, problem).

    Re-importing the same tag is the normal thing to do: it picks up whoever was
    tagged since, and refreshes the name and address of everyone already here
    from the CRM, which stays the home of both. It never removes anybody — a
    person leaving a tag is not the same as asking us to stop, and only the
    second one is ours to act on.
    """
    found, problem = crm.people(org_slug, tag_id)
    if problem:
        return 0, 0, problem

    known = {
        s.external_id: s
        for s in Subscriber.objects.filter(
            org_slug=org_slug, audience=audience, source=Subscriber.CRM
        )
    }
    # One person, one row: the CRM can hold the same address twice, and a list
    # that mails it twice looks broken to the person receiving it.
    seen_emails = set(subscribers(org_slug, audience).values_list("email", flat=True))
    added, refreshed, new_rows = 0, 0, []

    for person in found:
        existing = known.get(person["external_id"])
        if existing is not None:
            existing.email = person["email"]
            existing.name = person["name"]
            existing.via = tag_name
            existing.save(update_fields=["email", "name", "via", "refreshed_at"])
            refreshed += 1
            continue
        if person["email"] in seen_emails:
            continue
        seen_emails.add(person["email"])
        new_rows.append(
            Subscriber(
                org_slug=org_slug,
                audience=audience,
                source=Subscriber.CRM,
                external_id=person["external_id"],
                email=person["email"],
                name=person["name"],
                via=tag_name,
            )
        )
        added += 1

    Subscriber.objects.bulk_create(new_rows, ignore_conflicts=True)
    return added, refreshed, ""


def unsubscribe(org_slug: str, email: str, audience: str = SUPPORTERS) -> None:
    """One unsubscribe covers every list here, because it is keyed by address.

    A row is written when there is none. The cohort lists are GovKit's and have
    no rows at all, so without one there would be nowhere to remember that
    somebody on those lists said no.
    """
    email = email.strip().lower()
    if not email:
        return
    Subscriber.objects.filter(org_slug=org_slug, email=email, unsubscribed_at__isnull=True).update(
        unsubscribed_at=timezone.now()
    )
    Subscriber.objects.get_or_create(
        org_slug=org_slug,
        audience=audience,
        source=Subscriber.TYPED,
        external_id=email,
        defaults={"email": email, "unsubscribed_at": timezone.now()},
    )


# --- sending -----------------------------------------------------------------


def default_send_at(edition: Edition) -> datetime:
    """The Friday morning before the week it is about."""
    day = edition.window_start - timedelta(days=LEAD_DAYS)
    naive = datetime.combine(day, datetime.min.time()).replace(hour=9)
    return naive.replace(tzinfo=zone(edition))


def schedule(send: Send, when: datetime) -> None:
    send.scheduled_for = when
    send.save(update_fields=["scheduled_for", "updated_at"])


def unschedule(send: Send) -> None:
    """Back to being written. The page stays up if it was up — taking it down
    is its own button, and a link already shared should not die quietly."""
    send.scheduled_for = None
    send.sent_at = None
    send.save(update_fields=["scheduled_for", "sent_at", "updated_at"])


def publish(send: Send) -> str:
    """Put this week on its page and hand back the address.

    Its own act, because the email is often copied out and sent by hand: the
    page still has to exist, and its link still has to be pasteable into chat.
    """
    send.mint_token()
    send.published_at = send.published_at or timezone.now()
    send.save(update_fields=["public_token", "published_at", "updated_at"])
    return send.public_token


def unpublish(send: Send) -> None:
    """Take the page down. The token is kept, so putting it back is the same
    address rather than a second one loose in the world."""
    send.published_at = None
    send.save(update_fields=["published_at", "updated_at"])


def mark_sent(send: Send, recipients: int = 0) -> None:
    """A human pressed send. Sending also puts the week on its page, because
    that is where the email points."""
    publish(send)
    send.sent_at = timezone.now()
    send.recipients = recipients or send.recipients
    send.save(update_fields=["sent_at", "recipients", "updated_at"])


def stop_url(org_slug: str, audience: str) -> str:
    """Where a reader goes to be taken off this list.

    The same link in every copy: one email is written and pasted to everybody,
    so it cannot name the person. The page asks for the address instead.
    """
    from django.urls import reverse

    public = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    if not public:
        return ""
    return public + reverse(
        "comms_public:unsubscribe", kwargs={"org_slug": org_slug, "audience": audience}
    )


def email_html(edition: Edition, send: Send) -> str:
    """The email for this audience: theirs if they wrote one, ours if not.

    The generated draft is never thrown away and never stops being maintained,
    so returning to it hands back something current rather than something the
    week left behind.
    """
    if send.is_written_by_hand:
        return send.body_html
    return html_body(edition, send.audience, send.subject)


def write_by_hand(send: Send, html: str) -> None:
    """Take the email over. Blank hands it back to the generated draft."""
    send.body_html = (html or "").strip()
    send.save(update_fields=["body_html", "updated_at"])


def html_body(edition: Edition, audience: str, subject: str) -> str:
    """The email as HTML a person can paste straight into Gmail.

    Every style is inline. A mail client keeps no stylesheet and strips class
    names, so anything that lives in comms.css arrives as unstyled text — which
    is what "copy the text" gave you before. The link stays a link: that is the
    whole reason to paste rich text rather than plain.
    """
    out = [f'<div style="{_MAIL}">', f'<p style="{_SUBJECT}">{escape(subject)}</p>']
    for section in email(edition, audience):
        if section.get("title"):
            out.append(f'<h2 style="{_H2}">{escape(section["title"])}</h2>')
        out.append(f'<ul style="{_UL}">')
        for row in section["rows"]:
            when = " ".join(p for p in (row.get("day"), row.get("time")) if p)
            title = escape(row["title"])
            if row.get("href"):
                title = f'<a href="{escape(row["href"])}" style="{_LINK}">{title}</a>'
            line = f'<span style="{_WHEN}">{escape(when)}</span> ' if when else ""
            line += title
            if row.get("optional"):
                line += f' <span style="{_QUIET}">optional</span>'
            if row.get("note"):
                line += f'<br><span style="{_NOTE}">{escape(row["note"])}</span>'
            out.append(f'<li style="{_LI}">{line}</li>')
        out.append("</ul>")
    stop = stop_url(edition.org_slug, audience)
    if stop:
        out.append(
            f'<p style="{_STOP}">' f'<a href="{escape(stop)}" style="{_STOP}">Unsubscribe</a></p>'
        )
    out.append("</div>")
    return "".join(out)


# Inline styles for html_body, kept together so the email reads as one thing.
_MAIL = "font-family:Helvetica,Arial,sans-serif;font-size:14px;line-height:1.5;color:#1a1a1a"
_SUBJECT = "font-size:16px;font-weight:600;margin:0 0 12px"
_H2 = "font-size:15px;font-weight:600;margin:16px 0 4px"
_UL = "margin:0 0 12px;padding-left:20px"
_LI = "margin:4px 0"
_WHEN = "color:#555"
_LINK = "font-weight:600;color:#0b6b63;text-decoration:none"
_QUIET = "color:#777;font-size:13px"
_NOTE = "color:#555;font-size:13px"
_STOP = "color:#888;font-size:12px;margin:20px 0 0"


def plain_text_of(html: str, subject: str) -> str:
    """A hand-written email as plain text, for the copy that carries no markup."""
    from django.utils.html import strip_tags

    text = re.sub(r"<(br|/p|/div|/li|/h[1-6])[^>]*>", "\n", html, flags=re.I)
    text = strip_tags(text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return f"{subject}\n\n{text}\n"


def plain_text(edition: Edition, audience: str, subject: str) -> str:
    """The email as something a person can paste into a mail client."""
    lines = [subject, ""]
    for section in email(edition, audience):
        if section.get("title"):
            lines.append(section["title"])
        for row in section["rows"]:
            when = " ".join(p for p in (row.get("day"), row.get("time")) if p)
            head = f"{when}  {row['title']}" if when else row["title"]
            if row.get("optional"):
                head += " (optional)"
            lines.append(head)
            if row.get("note"):
                lines.append(f"    {row['note']}")
            if row.get("href"):
                lines.append(f"    {row['href']}")
        lines.append("")
    stop = stop_url(edition.org_slug, audience)
    if stop:
        lines.append(f"Unsubscribe: {stop}")
    return "\n".join(lines).strip() + "\n"
