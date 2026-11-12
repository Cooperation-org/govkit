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

    membership = Membership.objects.get(org=org_invite.org, user__email="g.lt@example.com")
    assert membership.user.auth_provider == "linkedtrust"
    assert membership.user.auth_provider_id == "lt-9"
    assert not Membership.objects.filter(user=google_user).exists()


# --- One step in through a shared link (no wall card) ----------------------------------


def _join(link):
    return reverse("orgs:join_link", kwargs={"code": link.code})


LT_INFO = {"sub": "lt-100", "email": "vol@example.com", "email_verified": True, "name": "Vol"}


@pytest.fixture
def lt_configured(settings):
    settings.LINKEDTRUST_CLIENT_ID = "test-client"
    settings.LINKEDTRUST_URL = "https://idp.example.com"
    return settings


@pytest.mark.django_db
def test_opening_a_team_link_signed_out_spends_nothing(client, team_link, lt_configured):
    from apps.orgs.invites import LINK_SESSION_KEY

    for _ in range(3):  # a link preview, a refresh, a second tab
        resp = client.get(_join(team_link))
        assert resp["Location"] == reverse("accounts:linkedtrust_start")
    assert client.session[LINK_SESSION_KEY] == team_link.code
    assert team_link.uses == 0
    assert not Invite.objects.filter(from_link=team_link).exists()


@pytest.mark.django_db
def test_team_link_joins_after_linkedtrust_with_no_wall_card(client, team_link, lt_configured):
    from apps.orgs.models import Membership

    client.get(_join(team_link))
    _linkedtrust_return(client, LT_INFO)

    m = Membership.objects.get(org=team_link.org, user__email="vol@example.com")
    assert m.role == MembershipRole.MEMBER
    assert m.user.auth_provider == "linkedtrust"
    invite = Invite.objects.get(from_link=team_link)
    assert invite.accepted_by == m.user
    assert invite.committed_claim_id is None
    assert invite.expires_at == team_link.expires_at
    assert team_link.uses == 1


@pytest.mark.django_db
def test_team_link_join_lands_on_the_cohort_front_door(client, team_link, lt_configured):
    lt_configured.COHORT_FRONT_DOOR = "https://front.example/dash/{org_slug}/"
    client.get(_join(team_link))
    resp = _linkedtrust_return(client, LT_INFO)
    assert resp["Location"] == "https://front.example/dash/rtv/"


@pytest.mark.django_db
def test_clicking_again_after_joining_spends_no_second_use(client, team_link, lt_configured):
    from apps.orgs.models import Membership

    client.get(_join(team_link))
    _linkedtrust_return(client, LT_INFO)
    for _ in range(3):
        client.get(_join(team_link))
    assert team_link.uses == 1
    assert Membership.objects.filter(org=team_link.org, user__email="vol@example.com").count() == 1


@pytest.mark.django_db(transaction=True)
def test_join_reports_the_membership_to_amebo(client, team_link, lt_configured):
    """Same provisioning hook as a personal invite: after commit, one call."""
    from unittest.mock import patch

    client.get(_join(team_link))
    with patch("apps.orgs.invites.provision_membership") as provision:
        _linkedtrust_return(client, LT_INFO)
    provision.assert_called_once()
    org, user, role = provision.call_args.args
    assert (org.slug, user.email, role) == ("rtv", "vol@example.com", MembershipRole.MEMBER)


@pytest.mark.django_db
def test_an_expired_link_is_refused_before_sign_in(client, team_link, lt_configured):
    team_link.expires_at = timezone.now() - timedelta(minutes=1)
    team_link.save(update_fields=["expires_at"])
    resp = client.get(_join(team_link), follow=True)
    assert resp.redirect_chain[0][0] == reverse("orgs:landing")
    assert "expired" in resp.content.decode()
    assert team_link.uses == 0


@pytest.mark.django_db
def test_a_closed_link_is_refused(client, team_link, lt_configured):
    team_link.active = False
    team_link.save(update_fields=["active"])
    resp = client.get(_join(team_link))
    assert resp["Location"] == reverse("orgs:landing")


@pytest.mark.django_db
def test_max_uses_is_enforced(client, team_link, lt_configured, user_factory):
    from apps.orgs.models import Membership

    team_link.max_uses = 1
    team_link.save(update_fields=["max_uses"])
    first = user_factory(email="a@example.com", auth_provider="linkedtrust", auth_provider_id="a")
    second = user_factory(email="b@example.com", auth_provider="linkedtrust", auth_provider_id="b")

    client.force_login(first)
    client.get(_join(team_link))
    client.force_login(second)
    resp = client.get(_join(team_link))

    assert Membership.objects.filter(org=team_link.org, user=first).exists()
    assert not Membership.objects.filter(org=team_link.org, user=second).exists()
    assert resp["Location"] == reverse("orgs:landing")
    assert team_link.uses == 1


@pytest.mark.django_db
def test_link_that_fills_during_sign_in_says_so(client, team_link, lt_configured, user_factory):
    from apps.orgs.models import Membership

    client.get(_join(team_link))  # signed out: waiting in the session
    team_link.max_uses = 0
    team_link.save(update_fields=["max_uses"])
    resp = _linkedtrust_return(client, LT_INFO)
    assert resp["Location"] == reverse("orgs:landing")
    assert not Membership.objects.filter(org=team_link.org, user__email="vol@example.com").exists()


@pytest.mark.django_db
def test_signed_in_without_linkedtrust_goes_through_linkedtrust(
    client, team_link, lt_configured, user_factory
):
    from apps.orgs.invites import LINK_SESSION_KEY
    from apps.orgs.models import Membership

    google_user = user_factory(
        email="g@example.com", password=None, auth_provider="google", auth_provider_id="g-1"
    )
    client.force_login(google_user)
    resp = client.get(_join(team_link))
    assert resp["Location"] == reverse("accounts:linkedtrust_start")
    assert "_auth_user_id" not in client.session
    assert client.session[LINK_SESSION_KEY] == team_link.code
    assert team_link.uses == 0
    assert not Membership.objects.filter(org=team_link.org, user=google_user).exists()


@pytest.mark.django_db
def test_existing_member_on_a_team_link_spends_nothing(client, admin_org, team_link, settings):
    settings.LINKEDTRUST_CLIENT_ID = "test-client"
    _, admin = admin_org  # signed in with a password, not LinkedTrust
    client.force_login(admin)
    client.get(_join(team_link))
    assert team_link.uses == 0
    assert client.session.get("_auth_user_id") == str(admin.pk)


@pytest.mark.django_db
def test_a_removed_member_is_not_let_back_in_by_the_same_link(
    client, team_link, lt_configured, user_factory
):
    from apps.orgs.models import Membership

    vol = user_factory(email="v@example.com", auth_provider="linkedtrust", auth_provider_id="v")
    client.force_login(vol)
    client.get(_join(team_link))
    Membership.objects.filter(org=team_link.org, user=vol).delete()

    resp = client.get(_join(team_link))
    assert resp["Location"] == reverse("orgs:landing")
    assert not Membership.objects.filter(org=team_link.org, user=vol).exists()
    assert team_link.uses == 1


@pytest.mark.django_db
def test_pool_link_screens_into_the_pool_with_no_wall_card(
    client, admin_org, lt_configured, user_factory
):
    from apps.orgs.models import InviteStatus, Membership

    org, _ = admin_org
    lt_configured.COHORT_POOL_LANDING = "https://front.example/pool/"
    pool_link = InviteLink.objects.create(org=org, kind=InviteKind.POOL)
    client.get(_join(pool_link))
    resp = _linkedtrust_return(client, LT_INFO)

    assert resp["Location"] == "https://front.example/pool/"
    invite = Invite.objects.get(from_link=pool_link)
    assert invite.kind == InviteKind.POOL
    assert invite.status == InviteStatus.ACCEPTED
    assert invite.committed_claim_id is None
    assert not Membership.objects.filter(user__email="vol@example.com").exists()

    client.get(_join(pool_link))  # again: no second applicant row
    assert pool_link.uses == 1


@pytest.mark.django_db
def test_an_unknown_code_is_refused(client):
    resp = client.get(reverse("orgs:join_link", kwargs={"code": "nope"}))
    assert resp["Location"] == reverse("orgs:landing")


@pytest.mark.django_db
def test_link_payload_carries_the_public_join_url(client, team_link, settings):
    settings.GOVKIT_S2S_TOKEN = S2S_TOKEN
    settings.PUBLIC_BASE_URL = "https://dash.example"
    body = client.get(
        reverse(
            "s2s_invite_link_detail",
            kwargs={"org_slug": team_link.org.slug, "code": team_link.code},
        ),
        **_auth(),
    ).json()
    assert body["join_url"] == f"https://dash.example/join/{team_link.code}/"
