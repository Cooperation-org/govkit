"""Invites (signed-token → login → Membership) and role/rate gating (UI + API)."""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.orgs.invites import make_invite_token
from apps.orgs.models import Membership, MembershipRole


@pytest.fixture
def admin_org(org_factory, user_factory, membership_factory):
    org = org_factory(slug="team")
    admin = user_factory(email="admin@example.com")
    membership_factory(org=org, user=admin, role=MembershipRole.ADMIN)
    return org, admin


# --- Invite → login → membership -------------------------------------------------------


@pytest.mark.django_db
def test_invite_link_then_login_creates_member(client, admin_org, user_factory, settings):
    settings.GOVKIT_DEV_LOGIN = True
    org, _ = admin_org
    token = make_invite_token(org, MembershipRole.MEMBER, email="invitee@example.com")

    # Anonymous visitor follows the invite link → routed to login, token stashed.
    resp = client.get(reverse("orgs:accept_invite"), {"token": token})
    assert resp.status_code == 302
    assert resp["Location"] == reverse("accounts:login")
    assert client.session.get("pending_invite_token") == token

    # They sign in (dev seam here); the pending invite is consumed on login.
    invitee = user_factory(email="invitee@example.com")
    resp = client.post(
        reverse("accounts:dev_login"),
        {"email": "invitee@example.com", "password": "pw12345!"},
    )
    assert resp.status_code == 302
    membership = Membership.objects.get(org=org, user=invitee)
    assert membership.role == MembershipRole.MEMBER
    assert resp["Location"] == reverse("orgs:dashboard", kwargs={"org_slug": org.slug})


@pytest.mark.django_db
def test_invite_accept_while_authenticated_joins_immediately(client, admin_org, user_factory):
    org, _ = admin_org
    token = make_invite_token(org, MembershipRole.STEWARD)
    user = user_factory()
    client.force_login(user)
    resp = client.get(reverse("orgs:accept_invite"), {"token": token})
    assert resp.status_code == 302
    assert Membership.objects.get(org=org, user=user).role == MembershipRole.STEWARD


@pytest.mark.django_db
def test_tampered_invite_rejected(client, admin_org, user_factory):
    org, _ = admin_org
    user = user_factory()
    client.force_login(user)
    resp = client.get(reverse("orgs:accept_invite"), {"token": "not-a-real-token"})
    assert resp.status_code == 302
    assert resp["Location"] == reverse("orgs:landing")
    assert not Membership.objects.filter(org=org, user=user).exists()


# --- Role gating (UI) ------------------------------------------------------------------


@pytest.mark.django_db
def test_admin_can_open_members_page(client, admin_org):
    org, admin = admin_org
    client.force_login(admin)
    resp = client.get(reverse("orgs:members", kwargs={"org_slug": org.slug}))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_member_cannot_open_members_page(client, admin_org, user_factory, membership_factory):
    org, _ = admin_org
    member = user_factory()
    membership_factory(org=org, user=member, role=MembershipRole.MEMBER)
    client.force_login(member)
    resp = client.get(reverse("orgs:members", kwargs={"org_slug": org.slug}))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_member_cannot_invite(client, admin_org, user_factory, membership_factory):
    org, _ = admin_org
    member = user_factory()
    membership_factory(org=org, user=member, role=MembershipRole.MEMBER)
    client.force_login(member)
    resp = client.post(
        reverse("orgs:invite_create", kwargs={"org_slug": org.slug}),
        {"email": "x@example.com", "role": MembershipRole.MEMBER},
    )
    assert resp.status_code == 403


@pytest.mark.django_db
def test_admin_sets_role_and_rate(client, admin_org, user_factory, membership_factory):
    org, admin = admin_org
    member = user_factory()
    m = membership_factory(org=org, user=member, role=MembershipRole.MEMBER)
    client.force_login(admin)
    resp = client.post(
        reverse("orgs:member_update", kwargs={"org_slug": org.slug, "membership_id": m.id}),
        {"role": MembershipRole.STEWARD, "hourly_rate": "75.50"},
    )
    assert resp.status_code == 302
    m.refresh_from_db()
    assert m.role == MembershipRole.STEWARD
    assert m.hourly_rate == Decimal("75.50")


@pytest.mark.django_db
def test_cannot_demote_last_admin(client, admin_org):
    org, admin = admin_org
    m = Membership.objects.get(org=org, user=admin)
    client.force_login(admin)
    resp = client.post(
        reverse("orgs:member_update", kwargs={"org_slug": org.slug, "membership_id": m.id}),
        {"role": MembershipRole.MEMBER},
    )
    assert resp.status_code == 302
    m.refresh_from_db()
    assert m.role == MembershipRole.ADMIN  # unchanged


@pytest.mark.django_db
def test_org_rate_update(client, admin_org):
    org, admin = admin_org
    client.force_login(admin)
    resp = client.post(
        reverse("orgs:org_rate", kwargs={"org_slug": org.slug}),
        {"default_hourly_rate": "42.00"},
    )
    assert resp.status_code == 302
    org.refresh_from_db()
    assert org.default_hourly_rate == Decimal("42.00")


# --- Role gating (API) -----------------------------------------------------------------


@pytest.mark.django_db
def test_api_member_cannot_change_role(client, admin_org, user_factory, membership_factory):
    org, _ = admin_org
    member = user_factory()
    m = membership_factory(org=org, user=member, role=MembershipRole.MEMBER)
    client.force_login(member)
    resp = client.patch(
        f"/api/v1/orgs/memberships/{m.id}/",
        {"role": MembershipRole.ADMIN},
        content_type="application/json",
    )
    assert resp.status_code == 403
    m.refresh_from_db()
    assert m.role == MembershipRole.MEMBER


@pytest.mark.django_db
def test_api_admin_can_change_role(client, admin_org, user_factory, membership_factory):
    org, admin = admin_org
    member = user_factory()
    m = membership_factory(org=org, user=member, role=MembershipRole.MEMBER)
    client.force_login(admin)
    resp = client.patch(
        f"/api/v1/orgs/memberships/{m.id}/",
        {"role": MembershipRole.STEWARD, "hourly_rate": "10.00"},
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    m.refresh_from_db()
    assert m.role == MembershipRole.STEWARD
    assert m.hourly_rate == Decimal("10.00")


@pytest.mark.django_db
def test_api_admin_can_invite(client, admin_org):
    org, admin = admin_org
    client.force_login(admin)
    resp = client.post(
        f"/api/v1/orgs/orgs/{org.slug}/invite/",
        {"email": "who@example.com", "role": MembershipRole.MEMBER},
        content_type="application/json",
    )
    assert resp.status_code == 201, resp.content
    assert "invite_link" in resp.json()


# --- L5: pay-rate visibility gated to admins / self ------------------------------------


@pytest.mark.django_db
def test_member_cannot_read_colleague_rate_via_members_list(
    client, admin_org, user_factory, membership_factory
):
    """A non-admin member sees their OWN rate but not a colleague's (L5)."""
    org, _ = admin_org
    me = user_factory(email="me@example.com")
    membership_factory(org=org, user=me, role=MembershipRole.MEMBER, hourly_rate=Decimal("40"))
    colleague = user_factory(email="colleague@example.com")
    membership_factory(
        org=org, user=colleague, role=MembershipRole.MEMBER, hourly_rate=Decimal("99")
    )

    client.force_login(me)
    resp = client.get(f"/api/v1/orgs/orgs/{org.slug}/members/")
    assert resp.status_code == 200, resp.content
    by_email = {row["email"]: row for row in resp.json()}
    # Own row exposes the rate.
    assert "hourly_rate" in by_email["me@example.com"]
    assert "effective_rate" in by_email["me@example.com"]
    # Colleague's row hides both rate fields.
    assert "hourly_rate" not in by_email["colleague@example.com"]
    assert "effective_rate" not in by_email["colleague@example.com"]


@pytest.mark.django_db
def test_member_cannot_retrieve_colleague_rate(client, admin_org, user_factory, membership_factory):
    org, _ = admin_org
    me = user_factory(email="me2@example.com")
    membership_factory(org=org, user=me, role=MembershipRole.MEMBER)
    colleague = user_factory(email="colleague2@example.com")
    cm = membership_factory(
        org=org, user=colleague, role=MembershipRole.MEMBER, hourly_rate=Decimal("99")
    )

    client.force_login(me)
    resp = client.get(f"/api/v1/orgs/memberships/{cm.id}/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert "hourly_rate" not in body
    assert "effective_rate" not in body


@pytest.mark.django_db
def test_admin_can_read_member_rate(client, admin_org, user_factory, membership_factory):
    org, admin = admin_org
    colleague = user_factory(email="colleague3@example.com")
    cm = membership_factory(
        org=org, user=colleague, role=MembershipRole.MEMBER, hourly_rate=Decimal("99")
    )
    client.force_login(admin)
    resp = client.get(f"/api/v1/orgs/memberships/{cm.id}/")
    assert resp.status_code == 200, resp.content
    assert resp.json()["hourly_rate"] == "99.00"
