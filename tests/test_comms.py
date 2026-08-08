"""
Comms — the week's email, per audience.

Two things these tests exist to hold. First the separability contract
(apps/comms/BOUNDARIES.md): comms may read GovKit through exactly one file, and
a stray `apps.orgs` import anywhere else is the thing that welds it in place, so
it fails the build rather than being noticed later. Second the cut rule: one line
is written once and cutting it from one audience must leave the others alone —
that is the whole reason items carry `off` instead of being copied per email.
"""

import json
import pathlib
import re
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.urls import reverse

from apps.comms import services
from apps.comms.models import AUDIENCE_KEYS, Edition, Send
from apps.orgs.models import MembershipRole

COMMS = pathlib.Path(__file__).resolve().parent.parent / "apps" / "comms"


# --- the separability contract ----------------------------------------------


def test_only_the_adapter_reaches_into_govkit():
    """One file names every GovKit fact comms uses. Nothing else imports apps.*."""
    stray = []
    for path in COMMS.rglob("*.py"):
        if path.name == "govkit.py" or "migrations" in path.parts:
            continue
        for line in path.read_text().splitlines():
            if re.match(r"\s*(from|import)\s+apps\.", line):
                stray.append(f"{path.relative_to(COMMS)}: {line.strip()}")
    assert not stray, "comms may only read GovKit through sources/govkit.py:\n" + "\n".join(stray)


def test_comms_owns_its_tables():
    assert Edition._meta.db_table.startswith("comms_")
    assert Send._meta.db_table.startswith("comms_")


# --- the calendar ------------------------------------------------------------

def ics_for(monday: date) -> str:
    """A cohort calendar for one week: a daily standup and a Monday kickoff.

    Built around the given Monday so the tests read whatever week they are run
    in, rather than passing on some days and failing on others.
    """
    day = monday.strftime("%Y%m%d")
    return f"""BEGIN:VCALENDAR
VERSION:2.0
X-WR-TIMEZONE:America/Phoenix
BEGIN:VEVENT
UID:standup@example
DTSTART:{day}T154500Z
DTEND:{day}T160000Z
RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR
SUMMARY:Standup
LOCATION:Zoom
END:VEVENT
BEGIN:VEVENT
UID:kickoff@example
DTSTART:{day}T160000Z
DTEND:{day}T170000Z
SUMMARY:Week 2 kickoff
END:VEVENT
END:VCALENDAR
"""


ICS = ics_for(date(2026, 8, 31))


class _Feed:
    def __init__(self, text):
        self.text = text.encode()

    def read(self, *_a):
        return self.text

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


@pytest.fixture(autouse=True)
def _no_cached_calendar():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def calendar_org(org_factory):
    org = org_factory(slug="vc")
    org.calendar_url = "https://calendar.google.com/calendar/ical/x/public/basic.ics"
    org.save(update_fields=["calendar_url"])
    return org


def test_a_repeating_meeting_becomes_one_line_per_day(calendar_org):
    """A weekly standup is five lines in the week, not one — that is the point."""
    with patch("urllib.request.urlopen", return_value=_Feed(ICS)):
        events, tz_name, problem = services.read_calendar(
            "vc", date(2026, 8, 31), date(2026, 9, 6)
        )
    assert problem == ""
    assert tz_name == "America/Phoenix"
    assert sum(1 for e in events if e.title == "Standup") == 5
    assert any(e.title == "Week 2 kickoff" for e in events)


def test_an_unreachable_calendar_is_a_sentence_not_a_crash(calendar_org):
    with patch("urllib.request.urlopen", side_effect=OSError("nope")):
        events, _tz, problem = services.read_calendar("vc", date(2026, 8, 31), date(2026, 9, 6))
    assert events == []
    assert "Could not reach" in problem


def test_the_window_is_the_week_that_has_not_started(calendar_org):
    start, end = services.week_window(date(2026, 8, 28))  # a Friday
    assert start.weekday() == 0
    assert start > date(2026, 8, 28)
    assert (end - start).days == 6


# --- the page ----------------------------------------------------------------


@pytest.fixture
def admin_at_vc(client, calendar_org, user_factory, membership_factory):
    user = user_factory()
    membership_factory(calendar_org, user, role=MembershipRole.ADMIN)
    client.force_login(user)
    return user


@pytest.fixture
def now_ics():
    """The calendar as it stands for the week the app would be writing about."""
    return ics_for(services.week_window()[0])


@pytest.fixture
def edition(calendar_org, now_ics):
    with patch("urllib.request.urlopen", return_value=_Feed(now_ics)):
        return services.open_edition("vc")


def test_an_ordinary_member_does_not_get_comms(
    client, calendar_org, user_factory, membership_factory, now_ics
):
    user = user_factory()
    membership_factory(calendar_org, user, role=MembershipRole.MEMBER)
    client.force_login(user)
    with patch("urllib.request.urlopen", return_value=_Feed(now_ics)):
        response = client.get(reverse("comms:index", kwargs={"org_slug": "vc"}))
    assert response.status_code == 403


def test_an_admin_sees_the_week_with_the_calendar_in_it(client, admin_at_vc, now_ics):
    with patch("urllib.request.urlopen", return_value=_Feed(now_ics)):
        response = client.get(reverse("comms:index", kwargs={"org_slug": "vc"}))
    assert response.status_code == 200
    assert b"Week 2 kickoff" in response.content


def test_every_audience_has_its_own_subject(edition):
    for audience in AUDIENCE_KEYS:
        send = services.send_for(edition, audience)
        assert send.subject
    assert edition.sends.count() == len(AUDIENCE_KEYS)


# --- cutting -----------------------------------------------------------------


def _calendar_rows(edition, audience):
    return next(s for s in services.email(edition, audience) if s["k"] == "cal")["rows"]


def test_cutting_a_line_from_one_email_leaves_the_others(edition):
    """The whole reason a line carries `off` instead of being copied three times."""
    rows = _calendar_rows(edition, "w")
    keep = [{"id": r["id"], "title": r["title"], "note": r["note"]} for r in rows[1:]]
    dropped = rows[0]["id"]

    services.save_section(edition, "w", "cal", keep)

    assert dropped not in [r["id"] for r in _calendar_rows(edition, "w")]
    assert dropped in [r["id"] for r in _calendar_rows(edition, "v")]
    assert dropped in [r["id"] for r in services.cut_items(edition, "w")]


def test_a_cut_line_comes_back_when_the_chip_is_pressed(edition):
    rows = _calendar_rows(edition, "w")
    dropped = rows[0]["id"]
    services.save_section(
        edition, "w", "cal", [{"id": r["id"], "title": r["title"], "note": r["note"]} for r in rows[1:]]
    )

    services.restore_item(edition, "w", dropped)

    assert dropped in [r["id"] for r in _calendar_rows(edition, "w")]
    assert services.cut_items(edition, "w") == []


def test_a_line_a_person_typed_is_kept_and_given_an_id(edition):
    rows = _calendar_rows(edition, "w")
    written = [{"id": r["id"], "title": r["title"], "note": r["note"]} for r in rows]
    written.append({"id": "", "title": "Bring your two minute presentation", "note": ""})

    changed = services.save_section(edition, "w", "cal", written)

    titles = [r["title"] for r in _calendar_rows(edition, "w")]
    assert "Bring your two minute presentation" in titles
    assert changed is True


def test_editing_words_alone_does_not_redraw_the_page(edition):
    rows = _calendar_rows(edition, "w")
    same = [{"id": r["id"], "title": r["title"] + " (Zoom)", "note": r["note"]} for r in rows]
    assert services.save_section(edition, "w", "cal", same) is False


def test_the_browser_saves_a_section_through_the_view(client, admin_at_vc, edition):
    rows = _calendar_rows(edition, "m")
    url = reverse("comms:save", kwargs={"org_slug": "vc", "pk": edition.pk})
    response = client.post(
        url,
        {
            "t": "m",
            "what": "cal",
            "rows": json.dumps([{"id": r["id"], "title": r["title"], "note": ""} for r in rows[1:]]),
        },
    )
    assert response.status_code == 200
    assert response.json()["redraw"] is True
    edition.refresh_from_db()
    assert len(_calendar_rows(edition, "m")) == len(rows) - 1


# --- sending -----------------------------------------------------------------


def test_the_page_is_not_there_until_it_is_sent(client, edition):
    send = services.send_for(edition, "w")
    send.mint_token()
    send.save(update_fields=["public_token"])
    url = reverse("comms_public:bulletin", kwargs={"token": send.public_token})
    assert client.get(url).status_code == 404


def test_sending_puts_the_week_on_its_page(client, admin_at_vc, edition):
    url = reverse("comms:send_now", kwargs={"org_slug": "vc", "pk": edition.pk})
    client.post(url, {"t": "w"})

    send = services.send_for(edition, "w")
    assert send.is_sent
    assert send.public_token
    page = client.get(reverse("comms_public:bulletin", kwargs={"token": send.public_token}))
    assert page.status_code == 200
    assert b"Week 2 kickoff" in page.content


def test_scheduling_writes_a_date_and_sends_nothing(client, admin_at_vc, edition):
    url = reverse("comms:schedule", kwargs={"org_slug": "vc", "pk": edition.pk})
    client.post(url, {"t": "v", "when": "2026-08-28T09:00"})

    send = services.send_for(edition, "v")
    assert send.scheduled_for is not None
    assert send.sent_at is None


def test_the_default_send_date_is_before_the_week_it_is_about(edition):
    when = services.default_send_at(edition)
    assert when.date() < edition.window_start
    assert edition.window_start - when.date() == timedelta(days=services.LEAD_DAYS)
