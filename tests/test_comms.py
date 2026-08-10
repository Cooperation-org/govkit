"""
Comms — the week's email, per audience.

Two things these tests exist to hold. First the separability contract
(apps/comms/BOUNDARIES.md): comms may read GovKit through exactly one file, and
a stray `apps.orgs` import anywhere else is the thing that welds it in place, so
it fails the build rather than being noticed later. Second the cut rule: one line
is written once and cutting it from one audience must leave the others alone —
that is the whole reason items carry `off` instead of being copied per email.
"""

import base64
import json
import pathlib
import re
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from apps.comms import calendar, services
from apps.comms.models import AUDIENCE_KEYS, MENTORS, SUPPORTERS, WORKERS, Edition, Send
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
        events, tz_name, problem = services.read_calendar("vc", date(2026, 8, 31), date(2026, 9, 6))
    assert problem == ""
    assert tz_name == "America/Phoenix"
    assert sum(1 for e in events if e.title == "Standup") == 5
    assert any(e.title == "Week 2 kickoff" for e in events)


def test_the_link_google_hands_a_person_finds_the_feed():
    """ "Get shareable link" gives a `cid=` link, and cid is the address base64'd.

    Passing the base64 through to the ICS endpoint is a 404, which is what the
    team saw after adding a calendar that was fine.
    """
    address = "abc123@group.calendar.google.com"
    cid = base64.b64encode(address.encode()).decode()
    assert calendar.ics_url_for(f"https://calendar.google.com/calendar/u/0?cid={cid}") == (
        "https://calendar.google.com/calendar/ical/"
        "abc123%40group.calendar.google.com/public/basic.ics"
    )


def test_reading_it_again_goes_back_to_the_calendar(client, admin_at_vc, edition, now_ics):
    """A meeting added a minute ago should not wait out the cache."""
    index = reverse("comms:index", kwargs={"org_slug": "vc"})
    refresh = reverse("comms:refresh_calendar", kwargs={"org_slug": "vc", "pk": edition.pk})
    with patch("urllib.request.urlopen", return_value=_Feed(now_ics)) as fetch:
        client.get(index, {"t": WORKERS})
        held = fetch.call_count
        client.get(index, {"t": WORKERS})
        assert fetch.call_count == held  # held for a few minutes
        client.post(refresh, {"t": WORKERS}, follow=True)
        assert fetch.call_count > held


def test_reading_it_again_takes_the_calendars_word_for_the_title(client, admin_at_vc, edition):
    """A calendar shared as free/busy says every meeting is called Busy. Fixing
    the sharing has to reach the lines already drafted, or the email still
    says Busy (golda 2026-08-10)."""
    refresh = reverse("comms:refresh_calendar", kwargs={"org_slug": "vc", "pk": edition.pk})
    row = _calendar_rows(edition, WORKERS)[0]
    services.save_section(
        edition, WORKERS, "cal", [{"id": row["id"], "title": row["title"], "note": "bring coffee"}]
    )
    edition.save()

    renamed = now_ics_text().replace("SUMMARY:Standup", "SUMMARY:Morning standup")
    cache.clear()
    with patch("urllib.request.urlopen", return_value=_Feed(renamed)):
        client.post(refresh, {"t": WORKERS}, follow=True)

    edition.refresh_from_db()
    titles = [i["title"] for i in edition.items if i.get("sec") == "cal"]
    assert "Morning standup" in titles
    assert "Standup" not in titles
    # What a person wrote about the meeting is theirs, and stays.
    assert edition.item(row["id"])["note"] == "bring coffee"


def test_a_meeting_line_goes_to_the_meeting(calendar_org):
    """Google leaves URL empty and puts the Meet link in X-GOOGLE-CONFERENCE,
    so every line used to fall back to the calendar as a whole."""
    monday = services.week_window()[0]
    ics = ics_for(monday).replace(
        "SUMMARY:Week 2 kickoff",
        "SUMMARY:Week 2 kickoff\nX-GOOGLE-CONFERENCE:https://meet.google.com/abc-defg-hij",
    )
    with patch("urllib.request.urlopen", return_value=_Feed(ics)):
        made = services.open_edition("vc")
    kickoff = next(i for i in made.items if i["title"] == "Week 2 kickoff")
    assert kickoff["href"] == "https://meet.google.com/abc-defg-hij"
    # A meeting with no way in still points somewhere useful.
    standup = next(i for i in made.items if i["title"] == "Standup")
    assert standup["href"] == calendar_org.calendar_url


def test_the_email_says_which_timezone_a_time_is_in(edition):
    """It goes to people in Phoenix, Lagos and Berlin at once."""
    times = [i["time"] for i in edition.items if i.get("sec") == "cal" and i["time"]]
    assert times
    assert all(t.endswith("MST") for t in times), times


def test_a_week_built_without_a_calendar_takes_one_when_it_arrives(calendar_org, now_ics):
    """The vc week of 2026-08-10 was built while the calendar 404'd, so its
    meetings only ever showed as leftovers under the email."""
    with patch("urllib.request.urlopen", side_effect=OSError("nope")):
        edition = services.open_edition("vc")
    assert edition.items == []

    cache.clear()
    with patch("urllib.request.urlopen", return_value=_Feed(now_ics)):
        edition = services.open_edition("vc")
    assert [i["title"] for i in edition.items if i["sec"] == "cal"]
    assert edition.tz_name == "America/Phoenix"


def test_a_calendar_someone_emptied_on_purpose_stays_empty(edition):
    """Cutting every line is an edit. It leaves the items in place carrying
    `off`, so refilling must not mistake it for a week that never read one."""
    rows = _calendar_rows(edition, WORKERS)
    assert rows
    services.save_section(edition, WORKERS, "cal", [])
    edition.save()
    assert _calendar_rows(edition, WORKERS) == []

    cache.clear()
    with patch("urllib.request.urlopen", return_value=_Feed(now_ics_text())):
        again = services.open_edition("vc")
    assert _calendar_rows(again, WORKERS) == []


def now_ics_text():
    return ics_for(services.week_window()[0])


def test_the_addresses_come_off_the_invites(calendar_org, user_factory):
    """Delivery is not wired, so the addresses have to be copyable. The invite
    is the join record: it says the address and which door they came through."""
    from apps.orgs.models import Invite, InviteStatus

    signed_in = user_factory(email="mentor-who-signed-in@example.com")
    Invite.objects.create(
        org=calendar_org, audience="mentor", kind="org", role="member",
        email="what-the-inviter-typed@example.com", accepted_by=signed_in,
        expires_at=timezone.now() + timedelta(days=30),
    )
    Invite.objects.create(
        org=calendar_org, audience="mentor", kind="org", role="member",
        email="not-clicked-yet@example.com",
        expires_at=timezone.now() + timedelta(days=30),
    )
    Invite.objects.create(
        org=calendar_org, audience="mentor", kind="org", role="member",
        email="changed-our-mind@example.com", status=InviteStatus.REVOKED,
        expires_at=timezone.now() + timedelta(days=30),
    )
    Invite.objects.create(
        org=calendar_org, audience="founder", kind="pool", role="member",
        email="in-the-pool@example.com",
        expires_at=timezone.now() + timedelta(days=30),
    )

    assert services.recipient_emails("vc", MENTORS) == [
        # The address they signed in with wins over the one typed for them.
        "mentor-who-signed-in@example.com",
        "not-clicked-yet@example.com",
    ]
    assert services.recipient_emails("vc", WORKERS) == ["in-the-pool@example.com"]


def test_one_unsubscribe_holds_for_a_list_govkit_hands_over(calendar_org):
    """models.Subscriber: one unsubscribe covers every list and every source."""
    from apps.comms.models import Subscriber
    from apps.orgs.models import Invite

    Invite.objects.create(
        org=calendar_org, audience="mentor", kind="org", role="member",
        email="no-thanks@example.com",
        expires_at=timezone.now() + timedelta(days=30),
    )
    Subscriber.objects.create(
        org_slug="vc", audience=SUPPORTERS, source="crm", external_id="1",
        email="no-thanks@example.com", unsubscribed_at=timezone.now(),
    )
    assert services.recipient_emails("vc", MENTORS) == []


def test_an_unreachable_calendar_is_a_sentence_not_a_crash(calendar_org):
    with patch("urllib.request.urlopen", side_effect=OSError("nope")):
        events, _tz, problem = services.read_calendar("vc", date(2026, 8, 31), date(2026, 9, 6))
    assert events == []
    assert "Could not reach" in problem


def test_the_window_starts_at_the_week_that_has_not_started_and_runs_a_fortnight(calendar_org):
    """People book travel and childcare further out than seven days, so the
    email lists two weeks and the too-early lines get cut (golda 2026-08-10)."""
    start, end = services.week_window(date(2026, 8, 28))  # a Friday
    assert start.weekday() == 0
    assert start > date(2026, 8, 28)
    assert (end - start).days == 13


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
        edition,
        "w",
        "cal",
        [{"id": r["id"], "title": r["title"], "note": r["note"]} for r in rows[1:]],
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
            "rows": json.dumps(
                [{"id": r["id"], "title": r["title"], "note": ""} for r in rows[1:]]
            ),
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


# --- Supporters: a list, not a role ------------------------------------------


def test_supporters_get_news_not_the_cohort_sections(edition):
    keys = [s["k"] for s in services.email(edition, "s")]
    assert "news" in keys
    assert "goals" not in keys and "opp" not in keys and "cal" not in keys


def test_supporters_subject_does_not_talk_about_the_week(edition):
    assert "week" not in services.default_subject(edition, "s").lower()
    assert "week" in services.default_subject(edition, "w").lower()


def test_importing_a_crm_tag_builds_the_list(calendar_org):
    found = [
        {"external_id": "5", "email": "a@example.com", "name": "A"},
        {"external_id": "9", "email": "b@example.com", "name": "B"},
    ]
    with patch("apps.comms.sources.crm.people", return_value=(found, "")):
        added, refreshed, problem = services.import_from_crm("vc", "s", 13, "Funder")

    assert (added, refreshed, problem) == (2, 0, "")
    assert services.audience_size("vc", "s") == 2
    assert set(services.subscribers("vc", "s").values_list("via", flat=True)) == {"Funder"}


def test_importing_again_refreshes_and_never_duplicates(calendar_org):
    first = [{"external_id": "5", "email": "a@example.com", "name": "A"}]
    with patch("apps.comms.sources.crm.people", return_value=(first, "")):
        services.import_from_crm("vc", "s", 13, "Funder")

    moved = [{"external_id": "5", "email": "new@example.com", "name": "A Renamed"}]
    with patch("apps.comms.sources.crm.people", return_value=(moved, "")):
        added, refreshed, _problem = services.import_from_crm("vc", "s", 13, "Funder")

    assert (added, refreshed) == (0, 1)
    row = services.subscribers("vc", "s").get()
    assert (row.email, row.name) == ("new@example.com", "A Renamed")


def test_one_address_twice_in_the_crm_is_one_row(calendar_org):
    twice = [
        {"external_id": "5", "email": "same@example.com", "name": "A"},
        {"external_id": "6", "email": "same@example.com", "name": "A again"},
    ]
    with patch("apps.comms.sources.crm.people", return_value=(twice, "")):
        added, _refreshed, _problem = services.import_from_crm("vc", "s", 13, "Funder")
    assert added == 1
    assert services.audience_size("vc", "s") == 1


def test_unsubscribing_is_by_address_so_it_covers_every_list(calendar_org):
    who = [{"external_id": "5", "email": "a@example.com", "name": "A"}]
    with patch("apps.comms.sources.crm.people", return_value=(who, "")):
        services.import_from_crm("vc", "s", 13, "Funder")
        services.import_from_crm("vc", "w", 13, "Funder")

    assert services.unsubscribe("vc", "A@Example.com ") == 2
    assert services.audience_size("vc", "s") == 0


def test_an_unreachable_crm_is_a_sentence_not_a_crash(calendar_org):
    with patch("apps.comms.sources.crm.people", return_value=([], "Could not read the CRM: no")):
        added, refreshed, problem = services.import_from_crm("vc", "s", 13, "Funder")
    assert (added, refreshed) == (0, 0)
    assert "Could not read" in problem


def test_the_standing_footer_carries_forward_to_next_week(calendar_org, now_ics):
    with patch("urllib.request.urlopen", return_value=_Feed(now_ics)):
        first = services.open_edition("vc")
    first.items.append(
        {
            **services.blank_item(first, "support"),
            "title": "Sponsor the cohort",
            "href": "https://workers.vc/sponsor",
        }
    )
    first.save(update_fields=["items"])

    with patch("urllib.request.urlopen", return_value=_Feed(now_ics)):
        later = services.open_edition("vc", today=first.window_start + timedelta(days=7))

    titles = [
        r["title"] for s in services.email(later, "s") if s["k"] == "support" for r in s["rows"]
    ]
    assert "Sponsor the cohort" in titles
    assert later.pk != first.pk


def test_each_org_imports_from_its_own_crm(settings):
    """A team's CRM is its own database, named after the team — never a shared one."""
    from apps.comms.sources import crm

    settings.COMMS_CRM_URL_PATTERN = "https://crm-{slug}.workers.vc"
    settings.COMMS_CRM_DB_PATTERN = "crm-{slug}"

    assert crm.where("vc") == ("https://crm-vc.workers.vc", "crm-vc")
    assert crm.where("kelp-route") == ("https://crm-kelp-route.workers.vc", "crm-kelp-route")
