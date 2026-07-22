"""Invites (magic-link code → login → Membership) and role/rate gating (UI + API)."""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.orgs.models import Invite, InviteStatus, Membership, MembershipRole


@pytest.fixture
def admin_org(org_factory, user_factory, membership_factory):
    org = org_factory(slug="team")
    admin = user_factory(email="admin@example.com")
    membership_factory(org=org, user=admin, role=MembershipRole.ADMIN)
    return org, admin


def _accept_url(invite):
    return reverse("orgs:accept_invite", kwargs={"code": invite.code})


# --- Invite → login → membership -------------------------------------------------------


@pytest.mark.django_db
def test_invite_link_then_login_creates_member(client, admin_org, user_factory, settings):
    settings.GOVKIT_DEV_LOGIN = True
    org, _ = admin_org
    invite = Invite.objects.create(org=org, role=MembershipRole.MEMBER, email="invitee@example.com", audience="mentor")

    # Anonymous visitor follows the invite link → the door renders, code stashed.
    # (The "sign in instead" side path below still finishes the join after login.)
    resp = client.get(_accept_url(invite))
    assert resp.status_code == 200
    assert client.session.get("pending_invite_code") == invite.code

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
    invite.refresh_from_db()
    assert invite.status == InviteStatus.ACCEPTED  # single-use: dead after the join


@pytest.mark.django_db
def test_invite_accept_while_authenticated_joins_immediately(client, admin_org, user_factory):
    org, _ = admin_org
    invite = Invite.objects.create(org=org, role=MembershipRole.STEWARD, name="Stew", audience="mentor")
    user = user_factory()
    client.force_login(user)
    resp = client.get(_accept_url(invite))
    assert resp.status_code == 302
    assert Membership.objects.get(org=org, user=user).role == MembershipRole.STEWARD
    invite.refresh_from_db()
    assert invite.status == InviteStatus.ACCEPTED


@pytest.mark.django_db
def test_unknown_invite_code_rejected(client, admin_org, user_factory):
    org, _ = admin_org
    user = user_factory()
    client.force_login(user)
    resp = client.get(reverse("orgs:accept_invite", kwargs={"code": "not-a-real-code"}))
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
        {"email": "x@example.com", "role": MembershipRole.MEMBER, "audience": "supporter"},
    )
    assert resp.status_code == 403
    assert not Invite.objects.exists()


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
    body = resp.json()
    assert "invite_link" in body
    invite = Invite.objects.get(code=body["code"])
    assert invite.org == org
    assert invite.email == "who@example.com"
    assert invite.status == InviteStatus.CREATED


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


# --- Member removal --------------------------------------------------------------------


def _remove_url(org, m):
    return reverse("orgs:member_remove", kwargs={"org_slug": org.slug, "membership_id": m.id})


@pytest.mark.django_db
def test_admin_removes_member(client, admin_org, user_factory, membership_factory):
    org, admin = admin_org
    member = user_factory(email="gone@example.com")
    m = membership_factory(org=org, user=member, role=MembershipRole.MEMBER)
    client.force_login(admin)
    resp = client.post(_remove_url(org, m))
    assert resp.status_code == 302
    assert not Membership.objects.filter(id=m.id).exists()
    member.refresh_from_db()  # the account itself survives


@pytest.mark.django_db
def test_member_cannot_remove(client, admin_org, user_factory, membership_factory):
    org, _ = admin_org
    member = user_factory()
    m = membership_factory(org=org, user=member, role=MembershipRole.MEMBER)
    client.force_login(member)
    resp = client.post(_remove_url(org, m))
    assert resp.status_code == 403
    assert Membership.objects.filter(id=m.id).exists()


@pytest.mark.django_db
def test_cannot_remove_last_admin(client, admin_org):
    org, admin = admin_org
    m = Membership.objects.get(org=org, user=admin)
    client.force_login(admin)
    resp = client.post(_remove_url(org, m))
    assert resp.status_code == 302
    assert Membership.objects.filter(id=m.id).exists()


@pytest.mark.django_db
def test_admin_can_remove_self_when_another_admin_remains(
    client, admin_org, user_factory, membership_factory
):
    org, admin = admin_org
    other = user_factory(email="other-admin@example.com")
    membership_factory(org=org, user=other, role=MembershipRole.ADMIN)
    m = Membership.objects.get(org=org, user=admin)
    client.force_login(admin)
    resp = client.post(_remove_url(org, m))
    assert resp.status_code == 302
    assert resp["Location"] == reverse("orgs:landing")
    assert not Membership.objects.filter(id=m.id).exists()


@pytest.mark.django_db
def test_member_with_earned_lines_is_protected(client, admin_org, user_factory, membership_factory):
    from apps.drops.models import DropLine, DropRun

    org, admin = admin_org
    member = user_factory(email="earned@example.com")
    m = membership_factory(org=org, user=member, role=MembershipRole.MEMBER)
    run = DropRun.objects.create(org=org)
    DropLine.objects.create(org=org, run=run, membership=m)

    client.force_login(admin)
    resp = client.post(_remove_url(org, m))
    assert resp.status_code == 302
    assert Membership.objects.filter(id=m.id).exists()  # refused, not deleted
