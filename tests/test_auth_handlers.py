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
def test_links_verified_email_to_existing_placeholder_user():
    # A provider-less, password-less placeholder (e.g. invited but never logged in) is
    # safe to claim by a VERIFIED email.
    existing = User.objects.create_user(email="person@example.com", password=None)
    assert existing.auth_provider_id == ""
    assert not existing.has_usable_password()
    user = get_or_create_user(_userinfo())
    assert user.pk == existing.pk
    assert user.auth_provider == "linkedtrust"
    assert user.auth_provider_id == "lt-sub-1"


@pytest.mark.django_db
def test_verified_email_does_not_take_over_password_account():
    # M2: an account with a real login credential must NOT be auto-claimed by a provider
    # asserting the same verified email — the owner must link explicitly.
    existing = User.objects.create_user(email="person@example.com", password="pw12345!")
    with pytest.raises(UserUpsertError):
        get_or_create_user(_userinfo())
    existing.refresh_from_db()
    assert existing.auth_provider_id == ""  # untouched


@pytest.mark.django_db
def test_verified_email_does_not_take_over_superuser():
    existing = User.objects.create_superuser(email="admin@example.com", password="pw12345!")
    with pytest.raises(UserUpsertError):
        get_or_create_user(_userinfo(email="admin@example.com"))
    existing.refresh_from_db()
    assert existing.auth_provider_id == ""


@pytest.mark.django_db
def test_verified_email_does_not_take_over_staff():
    existing = User.objects.create_user(email="staff@example.com", password=None, is_staff=True)
    # Even with no usable password, a staff flag alone blocks silent provider takeover.
    with pytest.raises(UserUpsertError):
        get_or_create_user(_userinfo(email="staff@example.com"))
    existing.refresh_from_db()
    assert existing.auth_provider_id == ""


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
