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


class TestAHandRaisedAtATeamReachesThatTeam:
    """The public join page (/ventures/<slug>/) sends people through the
    doorway, because someone joining a team has no account here yet. The team
    they came for rides along on the walk-up, so it reaches their rail — it
    used to be dropped, and the team never heard that anyone had knocked.
    """

    @pytest.fixture
    def doorway(self, settings, monkeypatch):
        """Stand in for the workers.vc doorway's S2S feed."""
        import json as json_mod
        import urllib.request
        from contextlib import contextmanager

        from django.core.cache import cache

        from apps.commons import attention

        cache.clear()
        settings.DOORWAY_API_URL = "http://doorway.test"
        settings.GOVKIT_S2S_TOKEN = "s2s"

        payload = {
            "pending": [
                {
                    "id": 1,
                    "person_name": "Golda Velez",
                    "role": "founder",
                    "created_at": "2026-07-29T01:12:38+00:00",
                    "approve_url": "http://doorway.test/admin/queue/",
                    "venture_slug": "northline",
                    "venture_name": "Northline Studio",
                },
                {
                    "id": 2,
                    "person_name": "Someone Else",
                    "role": "mentor",
                    "created_at": "2026-07-29T02:00:00+00:00",
                    "approve_url": "http://doorway.test/admin/queue/",
                    "venture_slug": "",
                    "venture_name": "",
                },
            ],
            "recent": [],
        }

        @contextmanager
        def fake_urlopen(req, timeout=None):
            class Resp:
                def read(inner):
                    return json_mod.dumps(payload).encode("utf-8")

            yield Resp()

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert attention is not None
        return payload

    def test_the_team_sees_who_knocked_on_their_door(
        self, client, doorway, org_factory, user_factory, membership_factory
    ):
        venture = org_factory(slug="northline", display_name="Northline Studio")
        _member(client, venture, user_factory, membership_factory)

        items = _rail(client, venture)

        assert [i["kind"] for i in items] == ["pool_pending"]
        assert items[0]["title"] == "Golda Velez is waiting at the door (founder)"

    def test_a_walk_up_for_another_team_is_not_their_business(
        self, client, doorway, org_factory, user_factory, membership_factory
    ):
        other = org_factory(slug="integralmass", display_name="IntegralMASS")
        _member(client, other, user_factory, membership_factory)

        assert _rail(client, other) == []

    def test_the_accelerator_queue_says_who_each_person_came_for(
        self, client, doorway, accel_org, settings, user_factory, membership_factory
    ):
        settings.DOORWAY_API_URL = "http://doorway.test"
        _member(client, accel_org, user_factory, membership_factory, MembershipRole.ADMIN)

        titles = [i["title"] for i in _rail(client, accel_org) if i["kind"] == "pool_pending"]

        assert "Golda Velez is waiting at the door for Northline Studio (founder)" in titles
        assert "Someone Else is waiting at the door (mentor)" in titles


def test_add_to_team_really_adds_them(
    client, org_factory, user_factory, membership_factory, settings
):
    """The yes on a hand-raise is a membership, not a dimmed row.

    Golda 2026-08-11: "Does 'mark answered' actually let him in, or just
    silence the row? It must let him in."
    """
    from apps.commons.models import VentureInterest
    from apps.orgs.models import Membership

    settings.ACCELERATOR_ORG_SLUG = ACCEL
    settings.DOORWAY_API_URL = ""
    org = org_factory(slug="teamx", display_name="Team X")
    admin = _member(client, org, user_factory, membership_factory, role=MembershipRole.ADMIN)
    wants_in = user_factory(email="mohammed@example.com")
    interest = VentureInterest.objects.create(org=org, user=wants_in, note="I build things")

    row = next(i for i in _rail(client, org) if i["kind"] == "venture_interest")
    assert row["accept_url"], "an admin must be offered the button that lets them in"

    resp = client.post(row["accept_url"], HTTP_X_GOVKIT_EMBED="1")

    assert resp.status_code == 200
    assert Membership.objects.filter(org=org, user=wants_in).exists()
    interest.refresh_from_db()
    assert interest.responded_at is not None
    assert interest.responded_by == admin


def test_only_an_admin_is_offered_the_way_in(
    client, org_factory, user_factory, membership_factory, settings
):
    """Adding someone to a team is the admin's to do, so nobody else is shown
    a button that would refuse them."""
    from apps.commons.models import VentureInterest
    from apps.orgs.models import Membership

    settings.ACCELERATOR_ORG_SLUG = ACCEL
    settings.DOORWAY_API_URL = ""
    org = org_factory(slug="teamy", display_name="Team Y")
    _member(client, org, user_factory, membership_factory)
    wants_in = user_factory(email="knocking@example.com")
    VentureInterest.objects.create(org=org, user=wants_in)

    row = next(i for i in _rail(client, org) if i["kind"] == "venture_interest")

    assert "accept_url" not in row
    resp = client.post(
        f"/api/v1/commons/orgs/{org.slug}/interest/{row['id']}/accept/",
        HTTP_X_GOVKIT_EMBED="1",
    )
    assert resp.status_code == 403
    assert not Membership.objects.filter(org=org, user=wants_in).exists()
