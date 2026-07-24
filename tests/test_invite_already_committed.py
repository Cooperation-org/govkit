"""The 'already committed' special case + BYOV → Admin default on invite mint.

Checked box: the invite is born COMMITTED so the doorway skips the attestation
(no new claim is written), yet accept still provisions the org and login attaches
the person to their existing account. Unchecked: the normal attestation flow.
"""

import pytest
from django.urls import reverse

from apps.orgs.forms import InviteForm
from apps.orgs.invites import accept_invite_for_user
from apps.orgs.models import (
    Invite,
    InviteKind,
    InviteStatus,
    MembershipRole,
    Org,
)


@pytest.fixture
def admin_org(org_factory, user_factory, membership_factory):
    org = org_factory(slug="team")
    admin = user_factory(email="admin@example.com")
    membership_factory(org=org, user=admin, role=MembershipRole.ADMIN)
    return org, admin


def _mint_url(org):
    return reverse("orgs:invite_create", kwargs={"org_slug": org.slug})


def _base_fields(**over):
    data = {
        "email": "invitee@example.com",
        "audience": "founder",
        "kind": InviteKind.ORG,
        "role": MembershipRole.MEMBER,
    }
    data.update(over)
    return data


# --- mint view: checkbox gates everything ---------------------------------------------


@pytest.mark.django_db
def test_unchecked_mints_a_normal_created_invite(client, admin_org):
    org, admin = admin_org
    client.force_login(admin)
    resp = client.post(_mint_url(org), _base_fields())
    assert resp.status_code == 302
    invite = Invite.objects.get(email="invitee@example.com")
    assert invite.status == InviteStatus.CREATED  # normal attestation flow
    assert invite.committed_claim_id is None


@pytest.mark.django_db
def test_already_committed_mints_committed_invite_without_a_claim(client, admin_org):
    org, admin = admin_org
    client.force_login(admin)
    resp = client.post(_mint_url(org), _base_fields(already_committed="on"))
    assert resp.status_code == 302
    invite = Invite.objects.get(email="invitee@example.com")
    assert invite.status == InviteStatus.COMMITTED  # doorway skips the attestation
    assert invite.committed_claim_id is None  # no claim written or linked


@pytest.mark.django_db
def test_already_committed_links_supplied_existing_claim(client, admin_org):
    org, admin = admin_org
    client.force_login(admin)
    resp = client.post(
        _mint_url(org),
        _base_fields(already_committed="on", committed_claim="https://live.linkedtrust.us/claims/1234/"),
    )
    assert resp.status_code == 302
    invite = Invite.objects.get(email="invitee@example.com")
    assert invite.status == InviteStatus.COMMITTED
    assert invite.committed_claim_id == 1234


@pytest.mark.django_db
def test_byov_invite_defaults_role_to_admin(client, admin_org):
    org, admin = admin_org
    client.force_login(admin)
    resp = client.post(
        _mint_url(org),
        _base_fields(name="Founder", kind=InviteKind.BYOV, role=MembershipRole.MEMBER, venture_name="IntegralMASS"),
    )
    assert resp.status_code == 302
    invite = Invite.objects.get(venture_name="IntegralMASS")
    assert invite.role == MembershipRole.ADMIN  # founder owns their venture


# --- accept still provisions the org for an already-committed BYOV invite -------------


@pytest.mark.django_db
def test_accept_committed_byov_provisions_org(admin_org, user_factory):
    org, _ = admin_org
    invite = Invite.objects.create(
        org=org, kind=InviteKind.BYOV, role=MembershipRole.ADMIN,
        audience="founder", name="Founder", venture_name="IntegralMASS",
    )
    invite.mark_committed()  # already committed, no claim
    assert invite.status == InviteStatus.COMMITTED

    user = user_factory(email="founder@example.com")
    _membership, venture_org = accept_invite_for_user(invite, user)

    assert venture_org is not None
    assert Org.objects.filter(display_name="IntegralMASS").exists()
    invite.refresh_from_db()
    assert invite.status == InviteStatus.ACCEPTED


# --- form validation ------------------------------------------------------------------


@pytest.mark.django_db
def test_committed_claim_without_checkbox_is_rejected():
    form = InviteForm(_base_fields(committed_claim="1234"))
    assert not form.is_valid()
    assert "committed_claim" in form.errors


@pytest.mark.django_db
def test_committed_claim_must_carry_an_id():
    form = InviteForm(_base_fields(already_committed="on", committed_claim="not-a-claim"))
    assert not form.is_valid()
    assert "committed_claim" in form.errors
