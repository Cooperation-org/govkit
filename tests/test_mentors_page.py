"""Mentors and the calendars they shared.

A mentor gave a booking link so teams could book them. It is kept off their
public wall card on purpose — offered to this cohort, not to the internet — so
the page lives behind sign-in and, for now, behind being a team admin (golda
2026-07-27): not pool people, not ordinary members.
"""

from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.orgs.models import MembershipRole

MENTORS = [
    {
        "claim_id": 124728,
        "person_name": "A Mentor",
        "role": "mentor",
        "email": "mentor@example.com",
        "calendar_url": "https://cal.example/mentor",
        "time_level": "an hour a week",
        "link": "https://linkedin.com/in/mentor",
    }
]


@pytest.fixture
def wall():
    with patch("apps.orgs.doorway._fetch_wall_people") as fetch:
        fetch.return_value = (
            MENTORS + [{"claim_id": 1, "person_name": "A Founder", "role": "founder"}],
            "",
        )
        yield fetch


def _as(client, user_factory, membership_factory, org, role):
    user = user_factory(email=f"{role}@example.com")
    if role is not None:
        membership_factory(org=org, user=user, role=role)
    client.force_login(user)
    return user


def test_a_team_admin_gets_the_booking_link(
    client, org_factory, user_factory, membership_factory, wall
):
    _as(client, user_factory, membership_factory, org_factory(slug="a"), MembershipRole.ADMIN)

    body = client.get(reverse("orgs:mentors")).content.decode()

    assert "https://cal.example/mentor" in body
    assert "an hour a week" in body


def test_only_mentors_are_listed(client, org_factory, user_factory, membership_factory, wall):
    _as(client, user_factory, membership_factory, org_factory(slug="a"), MembershipRole.ADMIN)

    body = client.get(reverse("orgs:mentors")).content.decode()

    assert 'claim-id="124728"' in body
    assert 'claim-id="1"' not in body


def test_an_ordinary_member_does_not_see_calendars_yet(
    client, org_factory, user_factory, membership_factory, wall
):
    _as(client, user_factory, membership_factory, org_factory(slug="a"), MembershipRole.MEMBER)

    resp = client.get(reverse("orgs:mentors"))

    assert resp.status_code == 403
    assert "cal.example" not in resp.content.decode()


def test_someone_with_no_org_at_all_does_not(client, user_factory, wall):
    client.force_login(user_factory(email="pool@example.com"))

    assert client.get(reverse("orgs:mentors")).status_code == 403


def test_signed_out_is_sent_to_sign_in(client, wall):
    resp = client.get(reverse("orgs:mentors"))

    assert resp.status_code == 302
    assert "cal.example" not in str(resp.headers)


def test_an_unreadable_wall_says_so_instead_of_looking_empty(
    client, org_factory, user_factory, membership_factory
):
    _as(client, user_factory, membership_factory, org_factory(slug="a"), MembershipRole.ADMIN)

    with patch(
        "apps.orgs.doorway._fetch_wall_people", return_value=([], "Could not read the wall")
    ):
        body = client.get(reverse("orgs:mentors")).content.decode()

    assert "Could not read the wall" in body
