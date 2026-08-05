"""
S2S: who a Discord user is, for the team's chat bot.

The bot needs a name and a role before it acts on anything a person types in
Discord. Identity has one home — the Membership row — so the bot asks here
rather than keeping its own map.
"""

import pytest
from django.urls import reverse

from apps.orgs.models import MembershipRole

TOKEN = "test-s2s-secret"


@pytest.fixture
def s2s(settings):
    settings.GOVKIT_S2S_TOKEN = TOKEN
    return {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}


@pytest.fixture
def steward(db, org_factory, user_factory, membership_factory):
    org = org_factory(slug="acme")
    user = user_factory(email="ann@example.com", display_name="Ann Lee")
    membership = membership_factory(
        org,
        user,
        role=MembershipRole.STEWARD,
        discord_user_id="1122334455",
        discord_username="annlee",
        taiga_username="ann",
    )
    return org, user, membership


def url(org_slug, discord_user_id):
    return reverse(
        "s2s_member_by_discord",
        kwargs={"org_slug": org_slug, "discord_user_id": discord_user_id},
    )


def test_returns_name_and_role(client, s2s, steward):
    org, user, _ = steward
    resp = client.get(url(org.slug, "1122334455"), **s2s)
    assert resp.status_code == 200
    body = resp.json()
    assert body["display_name"] == "Ann Lee"
    assert body["role"] == MembershipRole.STEWARD
    assert body["org_slug"] == "acme"
    assert body["taiga_username"] == "ann"
    assert body["email"] == user.email


def test_unknown_discord_user_is_404(client, s2s, steward):
    org, _, _ = steward
    assert client.get(url(org.slug, "9999999999"), **s2s).status_code == 404


def test_scoped_to_the_org_in_the_path(client, s2s, steward, org_factory):
    """A member of one org is not a member of another, same Discord id."""
    other = org_factory(slug="other")
    assert client.get(url(other.slug, "1122334455"), **s2s).status_code == 404


def test_requires_the_shared_secret(client, s2s, steward):
    org, _, _ = steward
    assert client.get(url(org.slug, "1122334455")).status_code == 401
    assert (
        client.get(url(org.slug, "1122334455"), HTTP_AUTHORIZATION="Bearer wrong").status_code
        == 401
    )


def test_disabled_when_no_token_is_configured(client, settings, steward):
    """An empty GOVKIT_S2S_TOKEN closes the door entirely."""
    settings.GOVKIT_S2S_TOKEN = ""
    org, _, _ = steward
    assert client.get(url(org.slug, "1122334455"), HTTP_AUTHORIZATION="Bearer ").status_code == 401


def test_falls_back_to_email_when_no_display_name(
    client, s2s, org_factory, user_factory, membership_factory
):
    org = org_factory(slug="nameless")
    user = user_factory(email="bo@example.com")
    membership_factory(org, user, discord_user_id="42")
    body = client.get(url(org.slug, "42"), **s2s).json()
    assert body["display_name"] == "bo@example.com"
    assert body["role"] == MembershipRole.MEMBER
