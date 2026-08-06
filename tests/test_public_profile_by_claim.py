"""Public profile by wall claim: the accepted invite row is the claim->person
link, so a workers.vc person page can show the member's opted-in profile
(bio, public links) under their card. Only opted-in fields serve; a claim
with no accepted invite behind it is a 404, not an empty profile."""

import pytest
from django.urls import reverse

from apps.accounts.models import ProfileLink, ProfileLinkKind
from apps.orgs.models import Invite, InviteKind, InviteStatus


@pytest.fixture
def accepted_invite(db, user_factory, org_factory):
    user = user_factory(email="ada@example.org", display_name="Ada Example")
    user.bio = "I build verification systems."
    user.save(update_fields=["bio"])
    org = org_factory()
    invite = Invite.objects.create(
        org=org,
        kind=InviteKind.POOL,
        status=InviteStatus.ACCEPTED,
        accepted_by=user,
        committed_claim_id=4242,
    )
    return user, invite


def test_profile_serves_by_claim(client, accepted_invite):
    user, invite = accepted_invite
    ProfileLink.objects.create(
        user=user,
        kind=ProfileLinkKind.GITHUB,
        url="https://github.com/ada",
        handle="ada",
        is_public=True,
    )
    ProfileLink.objects.create(
        user=user,
        kind=ProfileLinkKind.CALENDAR,
        url="https://cal.example/ada",
        is_public=False,
    )
    resp = client.get(reverse("public_profile_by_claim", args=[4242]))
    assert resp.status_code == 200
    data = resp.json()
    assert data["display_name"] == "Ada Example"
    assert data["bio"] == "I build verification systems."
    assert [link["kind"] for link in data["links"]] == ["github"]
    assert data["claim_ids"] == [4242]


def test_unknown_or_unaccepted_claim_404s(client, db, user_factory, org_factory):
    org = org_factory()
    Invite.objects.create(
        org=org,
        kind=InviteKind.POOL,
        status=InviteStatus.COMMITTED,
        committed_claim_id=5151,
    )
    assert client.get(reverse("public_profile_by_claim", args=[5151])).status_code == 404
    assert client.get(reverse("public_profile_by_claim", args=[999999])).status_code == 404
