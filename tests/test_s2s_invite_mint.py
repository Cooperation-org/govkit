"""S2S invite mint — the doorway's wall-approval path.

Someone who walked up to the wall attested before they had an account anywhere.
Approving them mints this invite: born committed against the claim they already
made, so no second attestation is asked for, and accept attaches that claim to
whatever account they sign in with.
"""

import json

import pytest

from apps.orgs.models import Invite, InviteKind, InviteStatus, MembershipRole


@pytest.fixture
def org(org_factory):
    return org_factory(slug="earnedgov")


def _post(client, org, body, token="s2s-secret"):
    return client.post(
        f"/api/v1/orgs/{org.slug}/invites/",
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )


def test_mint_returns_the_one_link_to_send(client, org, settings):
    settings.GOVKIT_S2S_TOKEN = "s2s-secret"
    resp = _post(
        client,
        org,
        {
            "audience": "mentor",
            "kind": InviteKind.ORG,
            "name": "Ada Example",
            "email": "ada@example.org",
            "committed_claim_id": 901,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    invite = Invite.objects.get(code=body["code"])
    assert invite.org == org
    assert invite.email == "ada@example.org"
    assert body["share_url"].endswith(f"{invite.code}/")


def test_the_claim_they_already_made_is_what_the_invite_carries(client, org, settings):
    settings.GOVKIT_S2S_TOKEN = "s2s-secret"
    resp = _post(client, org, {"audience": "mentor", "committed_claim_id": 901})
    invite = Invite.objects.get(code=resp.json()["code"])
    assert invite.status == InviteStatus.COMMITTED
    assert invite.committed_claim_id == 901


def test_without_a_claim_the_invite_still_asks_for_the_attestation(client, org, settings):
    settings.GOVKIT_S2S_TOKEN = "s2s-secret"
    resp = _post(client, org, {"audience": "mentor"})
    invite = Invite.objects.get(code=resp.json()["code"])
    assert invite.status == InviteStatus.CREATED
    assert invite.committed_claim_id is None


def test_a_founder_bringing_a_venture_is_its_admin(client, org, settings):
    settings.GOVKIT_S2S_TOKEN = "s2s-secret"
    resp = _post(
        client,
        org,
        {
            "audience": "founder",
            "kind": InviteKind.BYOV,
            "venture_name": "Northline Studio",
            "venture_url": "https://northlinestudio.us",
            "committed_claim_id": 124746,
        },
    )
    assert resp.status_code == 201
    invite = Invite.objects.get(code=resp.json()["code"])
    assert invite.kind == InviteKind.BYOV
    assert invite.venture_name == "Northline Studio"
    assert invite.role == MembershipRole.ADMIN


def test_a_nameless_venture_is_refused_rather_than_making_a_nameless_org(client, org, settings):
    settings.GOVKIT_S2S_TOKEN = "s2s-secret"
    resp = _post(client, org, {"audience": "founder", "kind": InviteKind.BYOV})
    assert resp.status_code == 400
    assert "venture_name" in resp.json()["error"]
    assert not Invite.objects.exists()


def test_an_unknown_audience_is_refused(client, org, settings):
    settings.GOVKIT_S2S_TOKEN = "s2s-secret"
    resp = _post(client, org, {"audience": "benefactor"})
    assert resp.status_code == 400
    assert not Invite.objects.exists()


def test_a_bad_token_mints_nothing(client, org, settings):
    settings.GOVKIT_S2S_TOKEN = "s2s-secret"
    resp = _post(client, org, {"audience": "mentor"}, token="wrong")
    assert resp.status_code == 401
    assert not Invite.objects.exists()


def test_an_unknown_org_is_a_404(client, org, settings):
    settings.GOVKIT_S2S_TOKEN = "s2s-secret"
    resp = client.post(
        "/api/v1/orgs/nobody/invites/",
        data=json.dumps({"audience": "mentor"}),
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer s2s-secret",
    )
    assert resp.status_code == 404
