"""
The cohort overview: program staff and mentors see every team's curriculum
progress at once, and a mentor gets that WITHOUT being given a governance role
(golda 2026-07-22 — a mentor's commitment is open calendar time, not stewardship).
"""

import pytest
from django.urls import reverse

from apps.orgs.cohorts import cohort_progress, item_skip_counts
from apps.orgs.genesis import MODULES, start_genesis, toggle_item
from apps.orgs.models import (
    Cohort,
    Invite,
    InviteStatus,
    MembershipRole,
)

FIRST_KEY = MODULES[0][3][0][0]


@pytest.fixture
def accelerator(org_factory):
    return org_factory(slug="workers", display_name="Workers Accelerator")


@pytest.fixture
def cohort(accelerator):
    return Cohort.objects.create(
        slug="autumn-2026", name="Autumn 2026", accelerator_org=accelerator
    )


@pytest.fixture
def teams(org_factory, cohort):
    """Two teams on the path, one team that never started."""
    on_path = []
    for slug in ("alpha", "beta"):
        org = org_factory(slug=slug, display_name=slug.title())
        org.cohort = cohort
        org.save(update_fields=["cohort"])
        start_genesis(org)
        on_path.append(org)
    idle = org_factory(slug="gamma", display_name="Gamma")
    idle.cohort = cohort
    idle.save(update_fields=["cohort"])
    return on_path, idle


def _url(cohort):
    return reverse("orgs:cohort_progress", kwargs={"cohort_slug": cohort.slug})


# --------------------------------------------------------------------------- who may look


def test_steward_of_the_accelerator_sees_every_team(
    client, cohort, teams, user_factory, membership_factory
):
    on_path, _ = teams
    staff = user_factory()
    membership_factory(org=cohort.accelerator_org, user=staff, role=MembershipRole.STEWARD)
    client.force_login(staff)

    resp = client.get(_url(cohort))
    assert resp.status_code == 200
    body = resp.content.decode()
    for org in on_path:
        assert org.display_name in body


def test_mentor_sees_progress_without_any_governance_role(client, cohort, teams, user_factory):
    """The invite that brought them in is the whole basis. No Membership at all."""
    mentor = user_factory()
    Invite.objects.create(
        org=cohort.accelerator_org,
        audience="mentor",
        name="M",
        status=InviteStatus.ACCEPTED,
        accepted_by=mentor,
    )
    client.force_login(mentor)

    resp = client.get(_url(cohort))
    assert resp.status_code == 200
    assert not cohort.accelerator_org.memberships.filter(user=mentor).exists()


def test_unaccepted_mentor_invite_is_not_access(client, cohort, teams, user_factory):
    mentor = user_factory()
    Invite.objects.create(org=cohort.accelerator_org, audience="mentor", name="M")
    client.force_login(mentor)
    assert client.get(_url(cohort)).status_code == 403


def test_funder_invite_does_not_open_the_overview(client, cohort, teams, user_factory):
    """Only the mentor audience. A funder accepted the same way sees nothing."""
    funder = user_factory()
    Invite.objects.create(
        org=cohort.accelerator_org,
        audience="funder",
        name="F",
        status=InviteStatus.ACCEPTED,
        accepted_by=funder,
    )
    client.force_login(funder)
    assert client.get(_url(cohort)).status_code == 403


def test_plain_team_member_cannot_see_other_teams(
    client, cohort, teams, user_factory, membership_factory
):
    on_path, _ = teams
    member = user_factory()
    membership_factory(org=on_path[0], user=member, role=MembershipRole.MEMBER)
    client.force_login(member)
    assert client.get(_url(cohort)).status_code == 403


def test_anonymous_is_sent_to_login(client, cohort, teams):
    resp = client.get(_url(cohort))
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"] or "next=" in resp.headers["Location"]


def test_unknown_cohort_404s(client, user_factory):
    user = user_factory(is_superuser=True, is_staff=True)
    client.force_login(user)
    assert (
        client.get(reverse("orgs:cohort_progress", kwargs={"cohort_slug": "nope"})).status_code
        == 404
    )


# --------------------------------------------------------------------------- what it says


@pytest.mark.django_db
def test_progress_covers_only_teams_on_the_path(cohort, teams, user_factory):
    on_path, idle = teams
    user = user_factory()
    toggle_item(on_path[0], FIRST_KEY, user)

    rows = cohort_progress(cohort)
    assert [row["org"].slug for row in rows] == [org.slug for org in on_path]
    assert idle.slug not in [row["org"].slug for row in rows]

    alpha = rows[0]
    assert alpha["done"] == 1
    assert alpha["total"] == sum(len(items) for _k, _l, _w, items in MODULES)


@pytest.mark.django_db
def test_skip_ranking_puts_untouched_items_first(cohort, teams, user_factory):
    on_path, _ = teams
    user = user_factory()
    for org in on_path:
        toggle_item(org, FIRST_KEY, user)

    skips = item_skip_counts(cohort)
    assert skips["teams"] == 2
    assert skips["ranking"][0]["done"] == 0  # nobody has touched it
    done_counts = {row["key"]: row["done"] for row in skips["ranking"]}
    assert done_counts[FIRST_KEY] == 2


@pytest.mark.django_db
def test_untick_removes_a_team_from_the_done_count(cohort, teams, user_factory):
    """Derived state follows the latest event; the tick itself is still on record."""
    from apps.orgs.models import ChecklistEvent

    on_path, _ = teams
    user = user_factory()
    toggle_item(on_path[0], FIRST_KEY, user)
    toggle_item(on_path[0], FIRST_KEY, user)

    skips = item_skip_counts(cohort)
    done_counts = {row["key"]: row["done"] for row in skips["ranking"]}
    assert done_counts[FIRST_KEY] == 0
    assert ChecklistEvent.objects.filter(org=on_path[0], item_key=FIRST_KEY).count() == 2
