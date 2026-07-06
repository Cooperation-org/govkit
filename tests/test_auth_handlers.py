"""Unit tests for the OAuth/OIDC user upsert (no HTTP, no client)."""

import pytest
from django.contrib.auth import get_user_model

from apps.accounts.auth_handlers import (
    UserUpsertError,
    get_or_create_user,
    upsert_oauth_user,
)

User = get_user_model()


def _userinfo(**over):
    base = {
        "sub": "lt-sub-1",
        "email": "person@example.com",
        "email_verified": True,
        "name": "Display Name",
        "picture": "https://cdn.example.com/a.png",
    }
    base.update(over)
    return base


@pytest.mark.django_db
def test_creates_user_keyed_on_subject():
    user = get_or_create_user(_userinfo())
    assert user.pk is not None
    assert user.auth_provider == "linkedtrust"
    assert user.auth_provider_id == "lt-sub-1"
    assert user.email == "person@example.com"
    assert user.display_name == "Display Name"
    assert not user.has_usable_password()


@pytest.mark.django_db
def test_second_login_same_subject_is_idempotent():
    first = get_or_create_user(_userinfo())
    second = get_or_create_user(_userinfo(name="Renamed"))
    assert first.pk == second.pk
    assert User.objects.count() == 1
    assert second.display_name == "Renamed"  # profile refreshed


@pytest.mark.django_db
def test_links_verified_email_to_existing_unclaimed_user():
    existing = User.objects.create_user(email="person@example.com", password="pw12345!")
    assert existing.auth_provider_id == ""
    user = get_or_create_user(_userinfo())
    assert user.pk == existing.pk
    assert user.auth_provider == "linkedtrust"
    assert user.auth_provider_id == "lt-sub-1"


@pytest.mark.django_db
def test_unverified_email_does_not_hijack_existing_account():
    User.objects.create_user(email="person@example.com", password="pw12345!")
    # Provider gives an UNVERIFIED email that collides — refuse rather than hijack.
    with pytest.raises(UserUpsertError):
        get_or_create_user(_userinfo(email_verified=False))
    assert User.objects.filter(email="person@example.com").count() == 1


@pytest.mark.django_db
def test_unverified_email_no_collision_creates_user():
    user = get_or_create_user(_userinfo(email_verified=False, email="fresh@example.com"))
    assert user.auth_provider_id == "lt-sub-1"
    assert user.email == "fresh@example.com"


@pytest.mark.django_db
def test_google_provider_recorded():
    user = upsert_oauth_user("google", _userinfo(sub="g-1"))
    assert user.auth_provider == "google"
    assert user.auth_provider_id == "g-1"


@pytest.mark.django_db
def test_missing_subject_raises():
    with pytest.raises(UserUpsertError):
        get_or_create_user(_userinfo(sub=""))
