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


# --- A team join needs a LinkedTrust identity -----------------------------------------


@pytest.fixture
def org_invite(admin_org):
    org, admin = admin_org
    return Invite.objects.create(org=org, kind=InviteKind.ORG, role=MembershipRole.MEMBER)


def _accept(invite):
    return reverse("orgs:accept_invite", kwargs={"code": invite.code})


@pytest.mark.django_db
def test_google_signed_in_person_goes_through_linkedtrust_before_joining(
    client, org_invite, user_factory, settings
):
    from apps.orgs.invites import SESSION_KEY
    from apps.orgs.models import Membership

    settings.LINKEDTRUST_CLIENT_ID = "test-client"
    google_user = user_factory(
        email="g@example.com", password=None, auth_provider="google", auth_provider_id="g-1"
    )
    client.force_login(google_user)
    resp = client.get(_accept(org_invite))

    assert resp.status_code == 302
    assert resp["Location"] == reverse("accounts:linkedtrust_start")
    assert not Membership.objects.filter(org=org_invite.org, user=google_user).exists()
    # Signed out, code kept for the join on the way back.
    assert "_auth_user_id" not in client.session
    assert client.session[SESSION_KEY] == org_invite.code
    org_invite.refresh_from_db()
    assert org_invite.can_accept


@pytest.mark.django_db
def test_linkedtrust_signed_in_person_joins_at_once(client, org_invite, user_factory, settings):
    from apps.orgs.models import Membership

    settings.LINKEDTRUST_CLIENT_ID = "test-client"
    lt_user = user_factory(
        email="lt@example.com", password=None, auth_provider="linkedtrust", auth_provider_id="s-1"
    )
    client.force_login(lt_user)
    client.get(_accept(org_invite))
    assert Membership.objects.filter(org=org_invite.org, user=lt_user).exists()


@pytest.mark.django_db
def test_existing_member_previewing_a_link_is_not_signed_out(
    client, admin_org, org_invite, settings
):
    """The admin checking their own mint stays signed in, whatever they signed in with."""
    settings.LINKEDTRUST_CLIENT_ID = "test-client"
    _, admin = admin_org
    client.force_login(admin)
    resp = client.get(_accept(org_invite))
    assert resp["Location"] != reverse("accounts:linkedtrust_start")
    assert client.session.get("_auth_user_id") == str(admin.pk)


@pytest.mark.django_db
def test_pool_invite_does_not_force_linkedtrust_on_a_signed_in_person(
    client, admin_org, user_factory, settings
):
    org, _ = admin_org
    settings.LINKEDTRUST_CLIENT_ID = "test-client"
    pool_invite = Invite.objects.create(org=org, kind=InviteKind.POOL)
    person = user_factory(email="p@example.com", auth_provider="google", auth_provider_id="g-2")
    client.force_login(person)
    client.get(_accept(pool_invite))
    pool_invite.refresh_from_db()
    assert pool_invite.accepted_by == person


@pytest.mark.django_db
def test_without_linkedtrust_configured_a_signed_in_person_joins_as_before(
    client, org_invite, user_factory
):
    from apps.orgs.models import Membership

    person = user_factory(email="s@example.com")
    client.force_login(person)
    client.get(_accept(org_invite))
    assert Membership.objects.filter(org=org_invite.org, user=person).exists()


def _linkedtrust_return(client, userinfo):
    """Come back from LinkedTrust as `userinfo` (IdP calls mocked)."""
    from unittest.mock import patch

    session = client.session
    session["oauth_state"] = "state-token"
    session.save()
    with (
        patch("apps.accounts.oidc.exchange_code", return_value={"access_token": "at"}),
        patch("apps.accounts.oidc.get_userinfo", return_value=userinfo),
    ):
        return client.get(
            reverse("accounts:linkedtrust_callback"), {"code": "abc", "state": "state-token"}
        )


@pytest.mark.django_db
def test_google_signed_in_person_is_joined_as_their_linkedtrust_identity(
    client, org_invite, user_factory, settings
):
    from apps.orgs.models import Membership

    settings.LINKEDTRUST_CLIENT_ID = "test-client"
    settings.LINKEDTRUST_URL = "https://idp.example.com"
    google_user = user_factory(
        email="g@example.com", password=None, auth_provider="google", auth_provider_id="g-1"
    )
    client.force_login(google_user)
    client.get(_accept(org_invite))
    _linkedtrust_return(
        client, {"sub": "lt-9", "email": "g.lt@example.com", "email_verified": True}
    )

    membership = Membership.objects.get(org=org_invite.org)
    assert membership.user.auth_provider == "linkedtrust"
    assert membership.user.auth_provider_id == "lt-9"
    assert not Membership.objects.filter(user=google_user).exists()
