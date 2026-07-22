"""
B1 one-hour team: founder invite accept auto-creates the venture org (admin membership,
default valuation config, genesis path started) and lands the founder there; the
dashboard renders the any-order module checklist; members toggle items. Also the B2
venture fields on the invite mint + S2S payload.
"""

import pytest
from django.urls import reverse

from apps.orgs.genesis import MODULES
from apps.orgs.models import (
    ChecklistEvent,
    Invite,
    InviteStatus,
    Membership,
    MembershipRole,
    Org,
)

S2S_TOKEN = "test-s2s-secret"


@pytest.fixture
def accel(org_factory):
    return org_factory(slug="accel", display_name="Accelerator")


@pytest.fixture
def founder_invite(accel):
    return Invite.objects.create(
        org=accel,
        role=MembershipRole.MEMBER,
        audience="founder",
        name="Fran Founder",
        venture_name="Integral Mass",
        venture_url="https://integralmass.example",
    )


def _accept(client, invite):
    return client.get(reverse("orgs:accept_invite", kwargs={"code": invite.code}))


def test_founder_accept_creates_venture_org(client, user_factory, founder_invite):
    user = user_factory(email="fran@example.com")
    client.force_login(user)
    resp = _accept(client, founder_invite)

    venture = Org.objects.filter(slug="integral-mass").first()
    assert venture is not None
    assert venture.display_name == "Integral Mass"
    assert venture.unit_name == "slices"
    assert venture.valuation_config is not None
    m = Membership.objects.get(org=venture, user=user)
    assert m.role == MembershipRole.ADMIN
    # Also joined the accelerator itself, with the invite's role.
    assert Membership.objects.filter(org=founder_invite.org, user=user).exists()
    founder_invite.refresh_from_db()
    assert founder_invite.status == InviteStatus.ACCEPTED
    # Lands on the venture, not the accelerator.
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/o/integral-mass/")


def test_founder_accept_starts_the_path(client, user_factory, founder_invite):
    """No copy of the curriculum is made: the org is simply marked as on the path."""
    from apps.orgs.genesis import modules_for

    user = user_factory()
    client.force_login(user)
    _accept(client, founder_invite)
    venture = Org.objects.get(slug="integral-mass")
    assert venture.genesis_started_at is not None
    assert not ChecklistEvent.objects.filter(org=venture).exists()

    modules = modules_for(venture)
    assert [m["key"] for m in modules] == [k for k, _l, _w, _i in MODULES]
    assert sum(m["total"] for m in modules) == sum(len(i) for _k, _l, _w, i in MODULES)
    assert all(m["done"] == 0 for m in modules)


def test_venture_slug_collision_gets_suffix(client, user_factory, org_factory, accel):
    org_factory(slug="integral-mass", display_name="Existing")
    invite = Invite.objects.create(
        org=accel, audience="founder", name="F", venture_name="Integral Mass"
    )
    user = user_factory()
    client.force_login(user)
    _accept(client, invite)
    assert Org.objects.filter(slug="integral-mass-2").exists()


def test_non_founder_accept_creates_no_org(client, user_factory, accel):
    invite = Invite.objects.create(
        org=accel, audience="mentor", name="M", venture_name="Should Not Matter"
    )
    user = user_factory()
    client.force_login(user)
    resp = _accept(client, invite)
    assert Org.objects.count() == 1  # just the accelerator
    assert resp.headers["Location"].endswith("/o/accel/")


def test_founder_without_venture_name_creates_no_org(client, user_factory, accel):
    invite = Invite.objects.create(org=accel, audience="founder", name="F")
    user = user_factory()
    client.force_login(user)
    _accept(client, invite)
    assert Org.objects.count() == 1


def test_dashboard_renders_module_index(client, user_factory, founder_invite):
    user = user_factory()
    client.force_login(user)
    _accept(client, founder_invite)
    resp = client.get(reverse("orgs:dashboard", kwargs={"org_slug": "integral-mass"}))
    assert resp.status_code == 200
    body = resp.content.decode()
    for key, _label, _week, _items in MODULES:
        assert f'id="module-{key}"' in body
    assert "gk-cyoa-index" in body


def test_member_toggles_checklist_item(client, user_factory, founder_invite):
    user = user_factory()
    client.force_login(user)
    _accept(client, founder_invite)
    venture = Org.objects.get(slug="integral-mass")
    key = MODULES[0][3][0][0]

    url = reverse(
        "orgs:checklist_toggle",
        kwargs={"org_slug": venture.slug, "item_key": key},
    )
    resp = client.post(url)
    assert resp.status_code == 302
    tick = ChecklistEvent.objects.get(org=venture, item_key=key)
    assert tick.action == "tick" and tick.actor == user
    assert tick.title_shown == MODULES[0][3][0][1]

    client.post(url)  # toggle back appends, never deletes
    assert list(
        ChecklistEvent.objects.filter(org=venture, item_key=key)
        .order_by("at", "id")
        .values_list("action", flat=True)
    ) == ["tick", "untick"]


def test_non_member_cannot_toggle(client, user_factory, founder_invite):
    founder = user_factory()
    client.force_login(founder)
    _accept(client, founder_invite)
    venture = Org.objects.get(slug="integral-mass")
    key = MODULES[0][3][0][0]

    outsider = user_factory()
    client.force_login(outsider)
    resp = client.post(
        reverse(
            "orgs:checklist_toggle",
            kwargs={"org_slug": venture.slug, "item_key": key},
        )
    )
    assert resp.status_code == 403
    assert not ChecklistEvent.objects.filter(org=venture).exists()


def test_s2s_payload_carries_venture_and_claim_fields(client, settings, founder_invite):
    settings.GOVKIT_S2S_TOKEN = S2S_TOKEN
    founder_invite.mark_committed(claim_id=77, statement_as_published="Our launch.")
    resp = client.get(
        reverse(
            "s2s_invite_detail",
            kwargs={"org_slug": founder_invite.org.slug, "code": founder_invite.code},
        ),
        HTTP_AUTHORIZATION=f"Bearer {S2S_TOKEN}",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["venture_name"] == "Integral Mass"
    assert data["venture_url"] == "https://integralmass.example"
    assert data["committed_claim_id"] == 77
    assert data["statement_as_published"] == "Our launch."


def test_onboarding_two_answers_is_enough(client, user_factory, db):
    user = user_factory()
    client.force_login(user)
    resp = client.post(
        reverse("orgs:onboarding"),
        {"display_name": "My Venture", "start_kind": "fresh"},
    )
    assert resp.status_code == 302
    org = Org.objects.get(slug="my-venture")
    assert org.unit_name == "points"
    assert org.valuation_config.valuation_mode == "hours_rate"


@pytest.mark.django_db
def test_admin_starts_path_for_existing_org(client, org_factory, user_factory, membership_factory):
    """Orgs that predate the curriculum get a one-click start (admin only)."""
    from apps.orgs.models import MembershipRole

    org = org_factory()
    admin = user_factory()
    membership_factory(org=org, user=admin, role=MembershipRole.ADMIN)
    client.force_login(admin)
    resp = client.post(f"/o/{org.slug}/checklist/seed/")
    assert resp.status_code == 302
    org.refresh_from_db()
    started = org.genesis_started_at
    assert started is not None
    # Idempotent: starting again keeps the original date.
    client.post(f"/o/{org.slug}/checklist/seed/")
    org.refresh_from_db()
    assert org.genesis_started_at == started


@pytest.mark.django_db
def test_member_cannot_seed_path(client, org_factory, user_factory, membership_factory):
    from apps.orgs.models import MembershipRole

    org = org_factory()
    member = user_factory()
    membership_factory(org=org, user=member, role=MembershipRole.MEMBER)
    client.force_login(member)
    resp = client.post(f"/o/{org.slug}/checklist/seed/")
    assert resp.status_code == 403
    org.refresh_from_db()
    assert org.genesis_started_at is None
