"""The CV a person hands over, and comes back to change.

It was uploaded at the door, before this account existed, so the doorway holds
it against their wall claim and nothing here keeps a copy. What is here is the
place they change it — their profile — and the place a team reads it: the pool,
behind sign-in, because a CV carries a home address and a phone number.
"""

import io
import json
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.orgs.models import Invite, InviteKind, InviteStatus


@pytest.fixture
def wired(settings):
    settings.DOORWAY_API_URL = "http://doorway.test"
    settings.GOVKIT_S2S_TOKEN = "s2s-secret"
    settings.COHORT_VIDEO_SRC = ""
    settings.COHORT_PERSON_URL = "https://workers.vc/p"


@pytest.fixture
def me(client, user_factory):
    user = user_factory(email="ada@example.com")
    client.force_login(user)
    return user


@pytest.fixture
def card(db, me, org_factory):
    return Invite.objects.create(
        org=org_factory(),
        email=me.email,
        kind=InviteKind.POOL,
        status=InviteStatus.ACCEPTED,
        accepted_by=me,
        committed_claim_id=500,
    )


def _doorway_says(payload=None, body=b"", headers=None):
    class _Resp:
        def __init__(self):
            self.headers = headers or {}

        def read(self):
            return json.dumps(payload).encode() if payload is not None else body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return patch("apps.orgs.doorway.urllib.request.urlopen", return_value=_Resp())


def _wall(resume_filename=""):
    return patch(
        "apps.orgs.doorway._fetch_wall_people",
        return_value=([{"claim_id": 500, "resume_filename": resume_filename}], ""),
    )


@pytest.mark.django_db
def test_someone_with_a_card_is_offered_a_place_to_put_their_cv(client, wired, card):
    with _wall():
        body = client.get(reverse("accounts:profile")).content.decode()
    assert "Your CV" in body
    assert "Add your CV" in body


@pytest.mark.django_db
def test_the_cv_they_have_is_named_and_can_be_replaced_or_taken_down(client, wired, card):
    with _wall("ada-cv.pdf"):
        body = client.get(reverse("accounts:profile")).content.decode()
    assert "ada-cv.pdf" in body
    assert "Replace it" in body
    assert "Take my CV down" in body


@pytest.mark.django_db
def test_someone_with_no_card_is_not_offered_one(client, wired, me):
    body = client.get(reverse("accounts:profile")).content.decode()
    assert "Your CV" not in body


@pytest.mark.django_db
def test_saving_a_cv_sends_the_file_to_the_doorway(client, wired, card):
    upload = SimpleUploadedFile("ada-cv.pdf", b"%PDF-1.4 me", content_type="application/pdf")
    with _doorway_says({"filename": "ada-cv.pdf", "size": 11}) as urlopen:
        resp = client.post(reverse("accounts:profile_cv"), {"cv": upload}, follow=True)

    assert resp.status_code == 200
    sent = urlopen.call_args_list[0].args[0]  # the write; the redirect then re-reads the wall
    assert sent.full_url == "http://doorway.test/api/wall/resume/500/"
    assert b"%PDF-1.4 me" in sent.data
    assert b'filename="ada-cv.pdf"' in sent.data
    assert "ada-cv.pdf is on your card" in resp.content.decode()


@pytest.mark.django_db
def test_what_the_doorway_refuses_is_what_the_person_is_told(client, wired, card):
    import urllib.error

    refusal = urllib.error.HTTPError(
        "http://doorway.test/api/wall/resume/500/",
        400,
        "Bad Request",
        {},
        io.BytesIO(json.dumps({"error": "Resumes go up as PDF, Word or plain text."}).encode()),
    )
    upload = SimpleUploadedFile("x.html", b"<script>", content_type="text/html")
    with patch("apps.orgs.doorway.urllib.request.urlopen", side_effect=refusal):
        resp = client.post(reverse("accounts:profile_cv"), {"cv": upload}, follow=True)

    assert "Resumes go up as PDF, Word or plain text." in resp.content.decode()


@pytest.mark.django_db
def test_taking_it_down_asks_the_doorway_to_remove_it(client, wired, card):
    with _doorway_says({"filename": "", "size": 0}) as urlopen:
        resp = client.post(reverse("accounts:profile_cv"), {"remove": "1"}, follow=True)

    assert urlopen.call_args_list[0].args[0].get_method() == "DELETE"
    assert "Your CV is off your card." in resp.content.decode()


@pytest.mark.django_db
def test_a_team_reads_the_cv_from_the_pool_card(client, wired, card):
    with _wall("ada-cv.pdf"):
        body = client.get(reverse("commons:pool")).content.decode()
    assert "Their CV (ada-cv.pdf)" in body
    assert reverse("commons:pool_cv", kwargs={"claim_id": 500}) in body


@pytest.mark.django_db
def test_the_file_is_handed_over_only_to_someone_signed_in(client, wired, card, user_factory):
    url = reverse("commons:pool_cv", kwargs={"claim_id": 500})
    client.logout()
    assert client.get(url).status_code == 302  # to the sign-in page

    client.force_login(user_factory(email="team@example.com"))
    with _doorway_says(
        body=b"%PDF-1.4 me",
        headers={
            "Content-Type": "application/pdf",
            "Content-Disposition": 'attachment; filename="ada-cv.pdf"',
        },
    ):
        got = client.get(url)
    assert got.status_code == 200
    assert b"".join(got.streaming_content) == b"%PDF-1.4 me"
    assert "ada-cv.pdf" in got["Content-Disposition"]
