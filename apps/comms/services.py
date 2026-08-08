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

import secrets
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import calendar as calendar_feed
from .models import (
    AUDIENCE_KEYS,
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


def week_window(today: date | None = None) -> tuple[date, date]:
    """(Monday, Sunday) of the week the next email is about."""
    today = today or timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    if (monday + timedelta(days=7) - today).days <= LEAD_DAYS + 1:
        monday += timedelta(days=7)
    elif today.weekday() >= 4:
        monday += timedelta(days=7)
    return monday, monday + timedelta(days=6)


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
    try:
        with transaction.atomic():
            edition.save()
    except IntegrityError:
        # Two tabs opened the same week at once; the first one wins.
        edition = Edition.objects.get(org_slug=org_slug, window_start=start)
    return edition


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
    events, _tz, problem = read_calendar(
        edition.org_slug, edition.window_start, edition.window_end
    )
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


def item_from_event(edition: Edition, event) -> dict:
    tz = zone(edition)
    local = event.starts.astimezone(tz)
    item = blank_item(edition, CALENDAR_SECTION)
    item.update(
        {
            "uid": event.uid,
            "starts": event.starts.isoformat(),
            "day": local.strftime("%a %-d"),
            "time": "" if event.all_day else local.strftime("%-I:%M%p").lower(),
            "title": event.title,
            "note": event.where,
            # A calendar row goes to the calendar. The event's own link when it
            # has one, the team's calendar when it does not.
            "href": event.url or govkit.calendar_url(edition.org_slug),
            "rec": True,
        }
    )
    return item


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


def import_from_crm(org_slug: str, audience: str, tag_id: int, tag_name: str):
    """Bring a CRM tag onto a list. Returns (added, refreshed, problem).

    Re-importing the same tag is the normal thing to do: it picks up whoever was
    tagged since, and refreshes the name and address of everyone already here
    from the CRM, which stays the home of both. It never removes anybody — a
    person leaving a tag is not the same as asking us to stop, and only the
    second one is ours to act on.
    """
    found, problem = crm.people(tag_id)
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
    seen_emails = set(
        subscribers(org_slug, audience).values_list("email", flat=True)
    )
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


def unsubscribe(org_slug: str, email: str) -> int:
    """One unsubscribe covers every list here, because it is keyed by address."""
    return Subscriber.objects.filter(
        org_slug=org_slug, email=email.strip().lower(), unsubscribed_at__isnull=True
    ).update(unsubscribed_at=timezone.now())


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
    send.scheduled_for = None
    send.sent_at = None
    send.save(update_fields=["scheduled_for", "sent_at", "updated_at"])


def mark_sent(send: Send, recipients: int = 0) -> None:
    """A human pressed send. Sending also puts the week on its page."""
    send.mint_token()
    send.sent_at = timezone.now()
    send.recipients = recipients or send.recipients
    send.save(
        update_fields=["public_token", "sent_at", "recipients", "updated_at"]
    )


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
    return "\n".join(lines).strip() + "\n"
