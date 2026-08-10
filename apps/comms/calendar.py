"""
Reading the upcoming meetings out of a calendar.

The calendar is the home of a meeting. Comms never stores one — it reads the
team's own ICS feed every time it draws a draft, so a meeting moved in Google
Calendar is moved here. What comms stores is the sentence a human wrote about
that meeting (apps.comms.models.Bulletin.items).

Recurrence is the whole job: a cohort calendar is mostly weekly standups and
office hours, so RRULE/EXDATE/RECURRENCE-ID have to be expanded correctly.
`icalendar` parses and `recurring_ical_events` expands; neither is worth
hand-rolling.

Fetch uses the standard library (`urllib`), like the repo's other outbound
calls; tests mock `urllib.request.urlopen`.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

import icalendar
import recurring_ical_events
from django.core.cache import cache

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 12
CACHE_SECONDS = 300
CACHE_PREFIX = "comms:ics:"
MAX_BYTES = 8 * 1024 * 1024


@dataclass
class Event:
    """One occurrence, already resolved out of any recurrence rule."""

    uid: str
    starts: datetime
    ends: datetime | None
    all_day: bool
    title: str
    where: str
    url: str
    detail: str


def _calendar_id(query: dict[str, list[str]]) -> str:
    """The calendar's own address out of a Google link's query string.

    `src=` carries the address as written. `cid=` carries the same address
    base64'd — that is the one "Get shareable link" hands a person, and feeding
    the base64 to the ICS endpoint is a 404, so it has to be decoded.
    """
    src = (query.get("src") or [""])[0]
    if src:
        return src
    cid = (query.get("cid") or [""])[0]
    if not cid:
        return ""
    try:
        decoded = base64.urlsafe_b64decode(cid + "=" * (-len(cid) % 4)).decode("utf-8")
    except Exception:
        return cid
    return decoded if "@" in decoded else cid


def ics_url_for(share_url: str) -> str:
    """The ICS feed behind whatever link a person pasted in the calendar box.

    Accepts the ICS address itself, a `webcal://` address, and the two Google
    Calendar links people actually copy: the embed link and the `cid=` link.
    Anything else is returned unchanged and either parses or reports a problem.
    """
    url = (share_url or "").strip()
    if not url:
        return ""
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://") :]
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.endswith("calendar.google.com") and not parsed.path.endswith(".ics"):
        calendar_id = _calendar_id(urllib.parse.parse_qs(parsed.query))
        if calendar_id:
            return (
                "https://calendar.google.com/calendar/ical/"
                f"{urllib.parse.quote(calendar_id, safe='')}/public/basic.ics"
            )
    return url


def read(share_url: str, start: date, end: date) -> tuple[list[Event], str, str]:
    """(events, calendar timezone name, problem) for occurrences in [start, end].

    `problem` is a sentence for the person looking at the page — an unreachable
    or unparseable calendar is a normal thing to see and correct, not a 500.
    """
    url = ics_url_for(share_url)
    if not url:
        return [], "", ""
    text, problem = _fetch(url)
    if problem:
        return [], "", problem
    try:
        cal = icalendar.Calendar.from_ical(text)
    except Exception as exc:
        logger.warning("comms: unparseable calendar at %s: %s", url, exc)
        return [], "", f"That calendar link did not come back as a calendar: {exc}"

    tz_name = str(cal.get("X-WR-TIMEZONE") or "")
    window_start = datetime.combine(start, time.min, tzinfo=timezone.utc)
    window_end = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
    try:
        occurrences = recurring_ical_events.of(cal).between(window_start, window_end)
    except Exception as exc:
        logger.warning("comms: could not expand recurrence for %s: %s", url, exc)
        return [], tz_name, f"Could not read the repeating events on that calendar: {exc}"

    events = [_event(o) for o in occurrences]
    events = [e for e in events if e is not None]
    events.sort(key=lambda e: e.starts)
    return events, tz_name, ""


def _cache_key(url: str) -> str:
    """A name for the cached copy that every worker agrees on.

    `hash()` on a string is salted per process, so each gunicorn worker would
    keep its own copy and none of them would see a refresh.
    """
    return CACHE_PREFIX + hashlib.sha256(url.encode("utf-8")).hexdigest()


def forget(share_url: str) -> None:
    """Drop the cached copy, so the next read goes back to the calendar."""
    url = ics_url_for(share_url)
    if url:
        cache.delete(_cache_key(url))


def _fetch(url: str) -> tuple[str, str]:
    key = _cache_key(url)
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "govkit-comms/1.0"})
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # nosec B310
            result = response.read(MAX_BYTES).decode("utf-8", "replace"), ""
    except Exception as exc:
        logger.warning("comms: calendar unreachable at %s: %s", url, exc)
        result = "", f"Could not reach that calendar: {exc}"
    cache.set(key, result, CACHE_SECONDS)
    return result


def _event(occurrence) -> Event | None:
    starts = occurrence.get("DTSTART")
    if starts is None:
        return None
    starts = starts.dt
    ends = occurrence.get("DTEND")
    ends = ends.dt if ends is not None else None

    all_day = not isinstance(starts, datetime)
    if all_day:
        starts = datetime.combine(starts, time.min, tzinfo=timezone.utc)
        if ends is not None and not isinstance(ends, datetime):
            ends = datetime.combine(ends, time.min, tzinfo=timezone.utc)
    if starts.tzinfo is None:
        starts = starts.replace(tzinfo=timezone.utc)
    if isinstance(ends, datetime) and ends.tzinfo is None:
        ends = ends.replace(tzinfo=timezone.utc)

    uid = str(occurrence.get("UID") or "")
    return Event(
        # One recurring series is one UID, so the occurrence's own start is what
        # makes "next Tuesday's standup" a different row from the one after it.
        uid=f"{uid}@{starts.isoformat()}",
        starts=starts,
        ends=ends if isinstance(ends, datetime) else None,
        all_day=all_day,
        title=str(occurrence.get("SUMMARY") or "").strip(),
        where=str(occurrence.get("LOCATION") or "").strip(),
        # What a person clicks a meeting for is the way into it. Google puts the
        # Meet link in X-GOOGLE-CONFERENCE and leaves URL empty, so a line that
        # read URL alone always fell back to the calendar as a whole.
        url=str(occurrence.get("X-GOOGLE-CONFERENCE") or occurrence.get("URL") or "").strip(),
        detail=str(occurrence.get("DESCRIPTION") or "").strip(),
    )
