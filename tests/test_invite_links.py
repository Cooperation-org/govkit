"""
Shared invite links (InviteLink): the invites they mint, the one-step join path,
and the team-link controls on the members page.
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.orgs.models import Invite, InviteKind, InviteLink, MembershipRole

S2S_TOKEN = "test-s2s-secret"


def _auth():
    return {"HTTP_AUTHORIZATION": f"Bearer {S2S_TOKEN}"}


@pytest.fixture
def admin_org(org_factory, user_factory, membership_factory):
    org = org_factory(slug="rtv", display_name="Raise the Voices")
    admin = user_factory(email="admin@example.com")
    membership_factory(org=org, user=admin, role=MembershipRole.ADMIN)
    return org, admin


@pytest.fixture
def team_link(admin_org):
    org, admin = admin_org
    return InviteLink.objects.create(
        org=org,
        kind=InviteKind.ORG,
        role=MembershipRole.MEMBER,
        expires_at=timezone.now() + timedelta(days=7),
        created_by=admin,
    )


# --- An invite minted through a link never outlives the link -------------------------


@pytest.mark.django_db
def test_link_invite_expiry_is_capped_at_the_link_expiry(team_link):
    assert team_link.invite_expires_at() == team_link.expires_at


@pytest.mark.django_db
def test_link_invite_expiry_is_the_usual_lifetime_when_the_link_lasts_longer(team_link):
    team_link.expires_at = timezone.now() + timedelta(days=365)
    expiry = team_link.invite_expires_at()
    assert expiry < team_link.expires_at
    assert abs((expiry - timezone.now()).days - 30) <= 1


@pytest.mark.django_db
def test_link_with_no_expiry_mints_the_usual_lifetime(team_link):
    team_link.expires_at = None
    assert abs((team_link.invite_expires_at() - timezone.now()).days - 30) <= 1


@pytest.mark.django_db
def test_s2s_mint_through_a_link_caps_the_invite_expiry(client, team_link, settings):
    settings.GOVKIT_S2S_TOKEN = S2S_TOKEN
    r = client.post(
        reverse("s2s_invite_mint", kwargs={"org_slug": team_link.org.slug}),
        {"claim_id": 501, "name": "Ana", "link_code": team_link.code},
        content_type="application/json",
        **_auth(),
    )
    assert r.status_code == 201
    invite = Invite.objects.get(from_link=team_link)
    assert invite.expires_at == team_link.expires_at
