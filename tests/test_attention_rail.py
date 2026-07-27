"""Who sees what on the dash's Activity rail.

The cohort filling up is the whole cohort's news, so every member of the
accelerator sees who has been joining (golda 2026-07-27). What stays with
admins is the work only they can do: hand-raises across every venture, and
walk-ups still pending approval at the doorway.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.orgs.models import Invite, InviteAudience, InviteStatus, MembershipRole

ACCEL = "accel"


@pytest.fixture
def accel_org(org_factory, settings):
    settings.ACCELERATOR_ORG_SLUG = ACCEL
    settings.DOORWAY_API_URL = ""  # no doorway in tests; walk-ups come from there
    return org_factory(slug=ACCEL, display_name="Workers VC")


@pytest.fixture
def joiner(accel_org, user_factory):
    """Someone who accepted an invite an hour ago."""
    return Invite.objects.create(
        org=accel_org,
        name="Newly Joined",
        audience=InviteAudience.MENTOR,
        status=InviteStatus.ACCEPTED,
        accepted_by=user_factory(email="joined@example.com"),
        accepted_at=timezone.now() - timedelta(hours=1),
    )


def _rail(client, org):
    return client.get(f"/api/v1/commons/orgs/{org.slug}/attention/").json()["items"]


def _member(client, org, user_factory, membership_factory, role=MembershipRole.MEMBER):
    user = user_factory(email=f"{role}@example.com")
    membership_factory(org=org, user=user, role=role)
    client.force_login(user)
    return user


def test_an_ordinary_member_sees_who_has_been_joining(
    client, accel_org, joiner, user_factory, membership_factory
):
    _member(client, accel_org, user_factory, membership_factory)

    items = _rail(client, accel_org)

    assert [i["kind"] for i in items] == ["invite_accepted"]
    assert "Newly Joined" in items[0]["title"] or "joined@example.com" in items[0]["title"]


def test_an_admin_sees_the_same_joins(client, accel_org, joiner, user_factory, membership_factory):
    _member(client, accel_org, user_factory, membership_factory, MembershipRole.ADMIN)

    kinds = [i["kind"] for i in _rail(client, accel_org)]

    assert "invite_accepted" in kinds


def test_a_ventures_rail_is_still_only_its_own_waiting_list(
    client, org_factory, joiner, user_factory, membership_factory
):
    """A venture's rail is about that venture. Accelerator news is not its news."""
    venture = org_factory(slug="northline", display_name="Northline Studio")
    _member(client, venture, user_factory, membership_factory)

    assert _rail(client, venture) == []


def test_a_non_member_gets_nothing(client, accel_org, joiner, user_factory):
    client.force_login(user_factory(email="outsider@example.com"))

    resp = client.get(f"/api/v1/commons/orgs/{accel_org.slug}/attention/")

    assert resp.status_code in (403, 404)


class TestWallPickerSaysWhyItIsEmpty:
    """A picker that renders nothing looks exactly like one that never shipped."""

    def test_an_unwired_doorway_says_so(self, settings):
        from apps.orgs.doorway import wall_people_without_accounts

        settings.DOORWAY_API_URL = ""
        settings.GOVKIT_S2S_TOKEN = "s2s"

        people, problem = wall_people_without_accounts()

        assert people == []
        assert "DOORWAY_API_URL" in problem

    def test_an_unreachable_doorway_names_the_host(self, settings):
        from django.core.cache import cache

        from apps.orgs.doorway import wall_people_without_accounts

        cache.clear()
        settings.DOORWAY_API_URL = "http://127.0.0.1:1"  # nothing listens here
        settings.GOVKIT_S2S_TOKEN = "s2s"

        people, problem = wall_people_without_accounts()

        assert people == []
        assert "127.0.0.1:1" in problem
