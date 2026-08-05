"""Putting a video on the wall card you already published.

The card is a signed claim on LinkedTrust, so it cannot be edited in place —
the doorway replaces it and keeps every link already shared pointing at the new
one. Our side holds the invite that carries the claim id, so all this does is
say which claim, hand over the video, and note the id that comes back.
"""

import json
from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.orgs.models import Invite, InviteKind, InviteStatus

VIDEO = "https://f003.backblazeb2.com/file/lt/v.webm"


@pytest.fixture
def wired(settings):
    settings.DOORWAY_API_URL = "http://doorway.test"
    settings.GOVKIT_S2S_TOKEN = "s2s-secret"
    settings.COHORT_VIDEO_SRC = "https://workers.vc/static/embed/video-recorder.js"
    settings.LINKEDTRUST_URL = "https://live.linkedtrust.us"


@pytest.fixture
def me(client, user_factory):
    user = user_factory(email="ada@example.com")
    client.force_login(user)
    return user


@pytest.fixture
def card(db, me, org_factory):
    """This person joined through the doorway, so an accepted invite of theirs
    carries the claim their card is."""
    return Invite.objects.create(
        org=org_factory(),
        email=me.email,
        kind=InviteKind.POOL,
        status=InviteStatus.ACCEPTED,
        accepted_by=me,
        committed_claim_id=500,
    )


def _fake_doorway(response):
    """Stand in for the doorway's HTTP reply to the republish call."""

    class _Resp:
        def read(self):
            return json.dumps(response).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return patch("apps.orgs.doorway.urllib.request.urlopen", return_value=_Resp())


@pytest.mark.django_db
def test_the_profile_offers_the_card_section_to_someone_who_has_a_card(client, wired, card):
    body = client.get(reverse("accounts:profile")).content.decode()
    assert "Your card on the wall" in body
    assert "video-recorder.js" in body


@pytest.mark.django_db
def test_someone_with_no_card_is_not_shown_one(client, wired, me):
    body = client.get(reverse("accounts:profile")).content.decode()
    assert "Your card on the wall" not in body


@pytest.mark.django_db
def test_the_section_stays_hidden_when_no_doorway_is_configured(client, settings, card):
    settings.DOORWAY_API_URL = ""
    settings.COHORT_VIDEO_SRC = ""
    body = client.get(reverse("accounts:profile")).content.decode()
    assert "Your card on the wall" not in body


@pytest.mark.django_db
def test_saving_a_video_republishes_and_follows_the_new_claim_id(client, wired, card):
    with _fake_doorway({"claim_id": 501, "page_url": "https://workers.vc/p/501/"}) as urlopen:
        resp = client.post(reverse("accounts:profile_card_video"), {"video_url": VIDEO})

    assert resp.status_code == 302
    sent = json.loads(urlopen.call_args.args[0].data.decode())
    assert sent == {"claim_id": 500, "video_url": VIDEO}
    card.refresh_from_db()
    # Our side must not keep pointing at a claim that no longer exists.
    assert card.committed_claim_id == 501
    assert card.video_url == VIDEO


@pytest.mark.django_db
def test_a_doorway_that_cannot_be_reached_says_so_and_changes_nothing(client, wired, card):
    with patch("apps.orgs.doorway.urllib.request.urlopen", side_effect=OSError("down")):
        resp = client.post(
            reverse("accounts:profile_card_video"), {"video_url": VIDEO}, follow=True
        )

    assert "could not update your card" in resp.content.decode()
    card.refresh_from_db()
    assert card.committed_claim_id == 500


@pytest.mark.django_db
def test_saving_with_no_video_is_refused_before_any_call(client, wired, card):
    with patch("apps.orgs.doorway.urllib.request.urlopen") as urlopen:
        resp = client.post(reverse("accounts:profile_card_video"), {"video_url": ""}, follow=True)
    urlopen.assert_not_called()
    assert "No video was uploaded" in resp.content.decode()


@pytest.mark.django_db
def test_someone_without_a_card_cannot_republish_one(client, wired, me):
    with patch("apps.orgs.doorway.urllib.request.urlopen") as urlopen:
        resp = client.post(
            reverse("accounts:profile_card_video"), {"video_url": VIDEO}, follow=True
        )
    urlopen.assert_not_called()
    # Django escapes the apostrophe in "don't", so match the plain part.
    assert "have a card on the wall yet" in resp.content.decode()


@pytest.mark.django_db
def test_it_is_a_post_only_route(client, wired, card):
    assert client.get(reverse("accounts:profile_card_video")).status_code == 405
