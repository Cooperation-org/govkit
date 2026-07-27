"""Uploading your own face.

Most people have a photo on their phone, not a URL for one. The bucket is the
same B2 one LinkedTrust puts claim media in (golda 2026-07-27), so a person's
face lives in one place however it arrived.
"""

from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

STORED_URL = "https://trustclaims-images.s3.us-east-005.backblazeb2.com/avatars/abc.jpg"


@pytest.fixture
def storage_on(settings):
    settings.GOVKIT_STORAGE_BUCKET = "trustclaims-images"
    settings.GOVKIT_STORAGE_KEY = "key"
    settings.GOVKIT_STORAGE_SECRET = "secret"
    settings.GOVKIT_STORAGE_REGION = "us-east-005"
    settings.GOVKIT_STORAGE_ENDPOINT = ""
    settings.GOVKIT_STORAGE_PUBLIC_URL = ""


@pytest.fixture
def me(client, user_factory):
    user = user_factory(email="me@example.com")
    client.force_login(user)
    return user


def _photo(name="face.jpg", content_type="image/jpeg", size=1024):
    return SimpleUploadedFile(name, b"x" * size, content_type=content_type)


def _post(client, **extra):
    data = {"display_name": "Me", "avatar_url": "", "bio": ""}
    data.update(extra)
    return client.post(reverse("accounts:profile"), data)


def test_an_uploaded_photo_becomes_the_persons_avatar(client, me, storage_on):
    with patch("apps.commons.storage.store_image", return_value=STORED_URL) as store:
        _post(client, photo=_photo())

    me.refresh_from_db()
    assert me.avatar_url == STORED_URL
    assert store.call_args.kwargs["prefix"] == "avatars"


def test_an_upload_replaces_a_url_typed_in_the_same_save(client, me, storage_on):
    """Two answers to one question; the photo they just chose wins."""
    with patch("apps.commons.storage.store_image", return_value=STORED_URL):
        _post(client, photo=_photo(), avatar_url="https://elsewhere.example/old.jpg")

    me.refresh_from_db()
    assert me.avatar_url == STORED_URL


def test_a_url_on_its_own_still_works(client, me, storage_on):
    _post(client, avatar_url="https://elsewhere.example/mine.jpg")

    me.refresh_from_db()
    assert me.avatar_url == "https://elsewhere.example/mine.jpg"


def test_a_pdf_is_refused_and_nothing_is_stored(client, me, storage_on):
    with patch("apps.commons.storage.store_image") as store:
        resp = _post(client, photo=_photo("cv.pdf", "application/pdf"))

    assert "JPEG, PNG, WebP or GIF" in resp.content.decode()
    store.assert_not_called()
    me.refresh_from_db()
    assert me.avatar_url == ""


def test_an_oversized_photo_is_refused(client, me, storage_on):
    with patch("apps.commons.storage.store_image") as store:
        resp = _post(client, photo=_photo(size=6 * 1024 * 1024))

    assert "limit is 5MB" in resp.content.decode()
    store.assert_not_called()


def test_without_storage_the_page_offers_a_url_instead_of_failing(client, me, settings):
    """No bucket: no file picker, and the URL field is still there to paste into.

    Asserted on what the page offers, not on its wording — the sentence
    explaining it is copy, and copy gets edited.
    """
    settings.GOVKIT_STORAGE_BUCKET = ""

    body = _post(client, photo=_photo()).content.decode()

    assert 'type="file"' not in body
    assert 'name="avatar_url"' in body


def test_a_failed_upload_changes_nothing_else(client, me, storage_on):
    with patch("apps.commons.storage.store_image", side_effect=RuntimeError("bucket gone")):
        resp = _post(client, photo=_photo(), display_name="New Name")

    assert "did not upload" in resp.content.decode()
    me.refresh_from_db()
    assert me.avatar_url == ""
    assert me.display_name != "New Name"


def test_the_key_is_ours_not_their_filename(storage_on):
    """An uploaded name is their words about their file, not a URL we build."""
    from apps.commons import storage

    with patch("apps.commons.storage._client") as client_factory:
        url = storage.store_image(_photo("../../etc/passwd.jpg"), prefix="avatars")

    key = client_factory.return_value.put_object.call_args.kwargs["Key"]
    assert key.startswith("avatars/")
    assert "passwd" not in key
    assert url.endswith(key)
