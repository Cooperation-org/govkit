"""Wherever GovKit shows a picture, a person can upload one or link one.

Four places ask for a picture: a member's own photo, a team's logo, the image
on a team's shared link, and the photo on an invite. They are asked the same
way, they go to the same bucket, and none of them can be handed a file that is
not a picture. These tests exist so the fifth one added is not asked
differently.
"""

from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.orgs.models import Invite, MembershipRole


@pytest.fixture
def bucket(settings):
    settings.GOVKIT_STORAGE_BUCKET = "bucket"
    settings.GOVKIT_STORAGE_KEY = "k"
    settings.GOVKIT_STORAGE_SECRET = "s"
    return settings


@pytest.fixture
def admin_org(org_factory, user_factory, membership_factory, client):
    org = org_factory(slug="acme", display_name="Acme")
    user = user_factory()
    membership_factory(org, user, role=MembershipRole.ADMIN)
    client.force_login(user)
    return org, user


def a_png(name="my picture.png"):
    return SimpleUploadedFile(name, b"\x89PNG\r\n\x1a\n" + b"0" * 64, "image/png")


def a_pdf():
    return SimpleUploadedFile("deck.pdf", b"%PDF-1.4", "application/pdf")


ORG_FIELDS = {
    "display_name": "Acme",
    "website": "",
    "tagline": "",
    "pitch": "",
    "looking_for": "",
    "logo_url": "",
    "cover_image_url": "",
    "socials": "",
}
INVITE_FIELDS = {
    "name": "Ada",
    "email": "ada@example.test",
    "link": "",
    "image_url": "",
    "audience": "founder",
    "kind": "pool",
    "role": "member",
    "venture_name": "",
    "venture_url": "",
    "drafted_statement": "",
    "drafted_social_post": "",
}


@pytest.mark.django_db
def test_every_screen_that_shows_a_picture_offers_an_upload(admin_org, client, bucket):
    org, _ = admin_org
    for url in (
        reverse("orgs:settings", kwargs={"org_slug": org.slug}),
        reverse("accounts:profile"),
        reverse("orgs:members", kwargs={"org_slug": org.slug}),
    ):
        body = client.get(url).content.decode()
        assert 'type="file"' in body, url
        assert 'accept="image/*"' in body, url
        # The link is still offered, under the upload.
        assert "Or link to one" in body, url
        # A form that cannot carry a file would drop the upload silently.
        assert 'enctype="multipart/form-data"' in body, url


@pytest.mark.django_db
@pytest.mark.parametrize(
    "field,folder",
    [("logo", "org-logos"), ("cover_image", "org-pictures")],
)
def test_a_teams_pictures_upload(admin_org, client, bucket, field, folder):
    org, _ = admin_org
    with patch(
        "apps.commons.pictures.storage.store_image",
        side_effect=lambda u, prefix: f"https://cdn.test/{prefix}/x.png",
    ) as put:
        client.post(
            reverse("orgs:settings", kwargs={"org_slug": org.slug}), {**ORG_FIELDS, field: a_png()}
        )
    org.refresh_from_db()
    stored = org.logo_url if field == "logo" else org.cover_image_url
    assert stored == f"https://cdn.test/{folder}/acme/x.png"
    # Never the name they gave the file: that is their words, not a URL.
    assert "my picture" not in put.call_args.kwargs["prefix"]


@pytest.mark.django_db
def test_a_members_own_photo_uploads(admin_org, client, bucket):
    _, user = admin_org
    with patch("apps.commons.pictures.storage.store_image", return_value="https://cdn.test/me.png"):
        client.post(
            reverse("accounts:profile"),
            {"display_name": "Ada", "avatar_url": "", "bio": "", "photo": a_png()},
        )
    user.refresh_from_db()
    assert user.avatar_url == "https://cdn.test/me.png"


@pytest.mark.django_db
def test_an_invites_photo_uploads(admin_org, client, bucket):
    org, _ = admin_org
    with patch(
        "apps.commons.pictures.storage.store_image", return_value="https://cdn.test/ada.png"
    ):
        client.post(
            reverse("orgs:invite_create", kwargs={"org_slug": org.slug}),
            {**INVITE_FIELDS, "image": a_png()},
        )
    assert Invite.objects.get(org=org).image_url == "https://cdn.test/ada.png"


@pytest.mark.django_db
def test_nothing_that_is_not_a_picture_is_ever_stored(admin_org, client, bucket):
    org, _ = admin_org
    posts = [
        (reverse("orgs:settings", kwargs={"org_slug": org.slug}), {**ORG_FIELDS, "logo": a_pdf()}),
        (
            reverse("orgs:settings", kwargs={"org_slug": org.slug}),
            {**ORG_FIELDS, "cover_image": a_pdf()},
        ),
        (
            reverse("accounts:profile"),
            {"display_name": "Ada", "avatar_url": "", "bio": "", "photo": a_pdf()},
        ),
        (
            reverse("orgs:invite_create", kwargs={"org_slug": org.slug}),
            {**INVITE_FIELDS, "image": a_pdf()},
        ),
    ]
    with patch("apps.commons.pictures.storage.store_image") as put:
        for url, data in posts:
            client.post(url, data)
    assert put.call_count == 0
    assert not Invite.objects.exists()


@pytest.mark.django_db
def test_with_no_bucket_the_screens_ask_for_a_link_and_say_nothing_technical(
    admin_org, client, settings
):
    org, _ = admin_org
    settings.GOVKIT_STORAGE_BUCKET = ""
    for url, url_field in (
        (reverse("orgs:settings", kwargs={"org_slug": org.slug}), "logo_url"),
        (reverse("accounts:profile"), "avatar_url"),
        (reverse("orgs:members", kwargs={"org_slug": org.slug}), "image_url"),
    ):
        body = client.get(url).content.decode()
        assert 'type="file"' not in body, url
        assert f'name="{url_field}"' in body, url
        # Never an env var name at a person.
        assert "GOVKIT_STORAGE" not in body, url
