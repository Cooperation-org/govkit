"""
B1 one-hour team: founder invite accept auto-creates the venture org (admin membership,
default valuation config, seeded genesis checklist) and lands the founder there; the
dashboard renders the any-order module checklist; members toggle items. Also the B2
venture fields on the invite mint + S2S payload.
"""

import pytest
from django.urls import reverse

from apps.orgs.genesis import MODULES
from apps.orgs.models import (
    ChecklistItem,
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


def test_founder_accept_seeds_genesis_checklist(client, user_factory, founder_invite):
    user = user_factory()
    client.force_login(user)
    _accept(client, founder_invite)
    venture = Org.objects.get(slug="integral-mass")
    items = ChecklistItem.objects.filter(org=venture)
    expected = sum(len(titles) for _k, _l, titles in MODULES)
    assert items.count() == expected
    assert set(items.values_list("module", flat=True)) == {k for k, _l, _t in MODULES}
    assert not items.filter(done_at__isnull=False).exists()


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
    for key, _label, _titles in MODULES:
        assert f'id="module-{key}"' in body
    assert "gk-cyoa-index" in body


def test_member_toggles_checklist_item(client, user_factory, founder_invite):
    user = user_factory()
    client.force_login(user)
    _accept(client, founder_invite)
    venture = Org.objects.get(slug="integral-mass")
    item = ChecklistItem.objects.filter(org=venture).first()

    url = reverse(
        "orgs:checklist_toggle",
        kwargs={"org_slug": venture.slug, "item_id": item.id},
    )
    resp = client.post(url)
    assert resp.status_code == 302
    item.refresh_from_db()
    assert item.done_at is not None
    assert item.done_by == user

    client.post(url)  # toggle back
    item.refresh_from_db()
    assert item.done_at is None
    assert item.done_by is None


def test_non_member_cannot_toggle(client, user_factory, founder_invite):
    founder = user_factory()
    client.force_login(founder)
    _accept(client, founder_invite)
    venture = Org.objects.get(slug="integral-mass")
    item = ChecklistItem.objects.filter(org=venture).first()

    outsider = user_factory()
    client.force_login(outsider)
    resp = client.post(
        reverse(
            "orgs:checklist_toggle",
            kwargs={"org_slug": venture.slug, "item_id": item.id},
        )
    )
    assert resp.status_code == 403
    item.refresh_from_db()
    assert item.done_at is None


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
