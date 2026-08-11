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
        "image": "https://img.example/mentor.jpg",
        "statement": "I will help teams find their first customers.",
        "date": "2026-07-02",
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
    # Drawn as our own card: their face and their words, no embedded component.
    assert "https://img.example/mentor.jpg" in body
    assert "I will help teams find their first customers." in body
    assert "linked-badge" not in body


def test_only_mentors_are_listed(client, org_factory, user_factory, membership_factory, wall):
    _as(client, user_factory, membership_factory, org_factory(slug="a"), MembershipRole.ADMIN)

    body = client.get(reverse("orgs:mentors")).content.decode()

    assert "A Mentor" in body
    assert "A Founder" not in body


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


def test_their_profile_words_win_over_what_they_joined_with(
    client, org_factory, user_factory, membership_factory, wall
):
    """A claim is signed and immutable; a profile is theirs to keep current."""
    from apps.orgs.models import Invite, InviteStatus

    org = org_factory(slug="a")
    mentor = user_factory(email="mentor@example.com")
    mentor.display_name = "Goes By This"
    mentor.bio = "What I actually help with now."
    mentor.avatar_url = "https://img.example/newer.jpg"
    mentor.save()
    Invite.objects.create(
        org=org, committed_claim_id=124728, accepted_by=mentor, status=InviteStatus.ACCEPTED
    )
    _as(client, user_factory, membership_factory, org, MembershipRole.ADMIN)

    body = client.get(reverse("orgs:mentors")).content.decode()

    assert "What I actually help with now." in body
    assert "I will help teams find their first customers." not in body
    assert "Goes By This" in body
    assert "https://img.example/newer.jpg" in body


def test_someone_who_never_signed_in_still_shows_their_wall_words(
    client, org_factory, user_factory, membership_factory, wall
):
    _as(client, user_factory, membership_factory, org_factory(slug="a"), MembershipRole.ADMIN)

    body = client.get(reverse("orgs:mentors")).content.decode()

    assert "I will help teams find their first customers." in body
    assert "A Mentor" in body


def test_an_empty_bio_falls_back_to_the_wall_rather_than_nothing(
    client, org_factory, user_factory, membership_factory, wall
):
    """Signing in must never blank out the words they already gave."""
    from apps.orgs.models import Invite, InviteStatus

    org = org_factory(slug="a")
    mentor = user_factory(email="quiet@example.com")
    Invite.objects.create(
        org=org, committed_claim_id=124728, accepted_by=mentor, status=InviteStatus.ACCEPTED
    )
    _as(client, user_factory, membership_factory, org, MembershipRole.ADMIN)

    body = client.get(reverse("orgs:mentors")).content.decode()

    assert "I will help teams find their first customers." in body


def test_a_mentor_can_change_the_booking_link_they_joined_with(
    client, org_factory, user_factory, membership_factory, wall
):
    """Calendar tools change; a signed claim cannot. So the link a mentor sets
    on their profile is the one teams get, and the old one is gone."""
    from apps.orgs.models import Invite, InviteStatus

    org = org_factory(slug="a")
    mentor = user_factory(email="mentor@example.com")
    mentor.calendar_url = "https://cal.example/moved-here"
    mentor.save()
    Invite.objects.create(
        org=org, committed_claim_id=124728, accepted_by=mentor, status=InviteStatus.ACCEPTED
    )
    _as(client, user_factory, membership_factory, org, MembershipRole.ADMIN)

    body = client.get(reverse("orgs:mentors")).content.decode()

    assert "https://cal.example/moved-here" in body
    assert "https://cal.example/mentor" not in body


def test_a_mentor_who_set_nothing_keeps_the_link_from_the_wall(
    client, org_factory, user_factory, membership_factory, wall
):
    """Signing in must never take away the calendar they already offered."""
    from apps.orgs.models import Invite, InviteStatus

    org = org_factory(slug="a")
    mentor = user_factory(email="quiet@example.com")
    Invite.objects.create(
        org=org, committed_claim_id=124728, accepted_by=mentor, status=InviteStatus.ACCEPTED
    )
    _as(client, user_factory, membership_factory, org, MembershipRole.ADMIN)

    body = client.get(reverse("orgs:mentors")).content.decode()

    assert "https://cal.example/mentor" in body


def test_the_profile_page_offers_the_booking_link(client, user_factory):
    """A mentor has to be able to find it without asking anyone."""
    mentor = user_factory(email="mentor@example.com")
    mentor.calendar_url = "https://cal.example/mine"
    mentor.save()
    client.force_login(mentor)

    body = client.get(reverse("accounts:profile")).content.decode()

    assert "Booking link" in body
    assert "https://cal.example/mine" in body


def test_saving_a_pasted_link_without_a_scheme_still_works(client, user_factory):
    """People paste 'calendly.com/ada'. Rejecting that is our problem, not theirs."""
    mentor = user_factory(email="mentor@example.com")
    client.force_login(mentor)

    resp = client.post(
        reverse("accounts:profile"),
        {"display_name": "Ada", "avatar_url": "", "bio": "", "calendar_url": "calendly.com/ada"},
    )

    assert resp.status_code == 302
    mentor.refresh_from_db()
    assert mentor.calendar_url == "https://calendly.com/ada"
