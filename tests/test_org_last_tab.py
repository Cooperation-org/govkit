"""Coming back to an org lands where you left off.

Outside menus point at /o/<slug>/open/ because they cannot know where someone
was working. It used to be hardcoded to the pie, so anyone living on Drops or
Members paid a click every time (golda 2026-07-27).
"""

import pytest
from django.urls import reverse

from apps.orgs.models import MembershipRole


@pytest.fixture
def team(org_factory, user_factory, membership_factory):
    org = org_factory(slug="northline")
    user = user_factory(email="member@example.com")
    membership_factory(org=org, user=user, role=MembershipRole.MEMBER)
    return org, user


def _open(client, org):
    return client.get(reverse("orgs:open_org", kwargs={"org_slug": org.slug}))


def test_the_pie_is_where_a_first_visit_lands(client, team):
    org, user = team
    client.force_login(user)

    assert _open(client, org).url == reverse("pie:index", kwargs={"org_slug": org.slug})


def test_it_remembers_the_tab_you_were_last_on(client, team):
    org, user = team
    client.force_login(user)
    client.get(reverse("drops:index", kwargs={"org_slug": org.slug}))

    assert _open(client, org).url == reverse("drops:index", kwargs={"org_slug": org.slug})


def test_each_org_is_remembered_on_its_own(client, team, org_factory, membership_factory):
    org, user = team
    other = org_factory(slug="linked-trust")
    membership_factory(org=other, user=user, role=MembershipRole.MEMBER)
    client.force_login(user)
    client.get(reverse("drops:index", kwargs={"org_slug": org.slug}))
    client.get(reverse("votes:index", kwargs={"org_slug": other.slug}))

    assert _open(client, org).url == reverse("drops:index", kwargs={"org_slug": org.slug})
    assert _open(client, other).url == reverse("votes:index", kwargs={"org_slug": other.slug})


def test_a_tab_you_can_no_longer_reach_is_not_where_you_land(client, team, membership_factory):
    """Members is an admin tab. Losing admin must not strand you on a 403."""
    org, user = team
    membership = org.memberships.get(user=user)
    membership.role = MembershipRole.ADMIN
    membership.save(update_fields=["role"])
    client.force_login(user)
    client.get(reverse("orgs:members", kwargs={"org_slug": org.slug}))

    membership.role = MembershipRole.MEMBER
    membership.save(update_fields=["role"])

    assert _open(client, org).url == reverse("pie:index", kwargs={"org_slug": org.slug})


def test_signed_out_people_are_sent_to_sign_in(client, team):
    org, _ = team

    resp = _open(client, org)

    assert resp.status_code == 302
    assert "login" in resp.url or "signin" in resp.url
