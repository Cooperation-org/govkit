"""
Login-flow tests with all IdP/Google HTTP calls MOCKED.

Covers: the login page renders both provider buttons; the LinkedTrust OIDC callback
creates + session-logs-in a user; the Google id-token path creates a user; state
validation rejects a mismatched callback; the dev-login seam is gated.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

User = get_user_model()


@pytest.fixture
def configured(settings):
    settings.LINKEDTRUST_CLIENT_ID = "lt-client"
    settings.LINKEDTRUST_URL = "https://idp.example.com"
    settings.GOOGLE_OAUTH_CLIENT_ID = "google-client"
    settings.GOOGLE_OAUTH_CLIENT_SECRET = "google-secret"
    return settings


def _set_state(client, state="state-token"):
    session = client.session
    session["oauth_state"] = state
    session.save()
    return state


@pytest.mark.django_db
def test_login_page_shows_both_providers(client, configured):
    resp = client.get(reverse("accounts:login"))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert reverse("accounts:linkedtrust_start") in body
    assert reverse("accounts:google_start") in body


@pytest.mark.django_db
def test_linkedtrust_start_redirects_to_idp(client, configured):
    resp = client.get(reverse("accounts:linkedtrust_start"))
    assert resp.status_code == 302
    assert resp["Location"].startswith("https://idp.example.com/oauth/authorize")
    # state was stashed for later verification
    assert client.session.get("oauth_state")


@pytest.mark.django_db
def test_linkedtrust_callback_creates_and_logs_in_user(client, configured):
    state = _set_state(client)
    userinfo = {
        "sub": "lt-42",
        "email": "new@example.com",
        "email_verified": True,
        "name": "New Person",
    }
    with (
        patch("apps.accounts.oidc.exchange_code", return_value={"access_token": "at"}) as ex,
        patch("apps.accounts.oidc.get_userinfo", return_value=userinfo) as ui,
    ):
        resp = client.get(reverse("accounts:linkedtrust_callback"), {"code": "abc", "state": state})
    ex.assert_called_once()
    ui.assert_called_once_with("at")
    assert resp.status_code == 302
    user = User.objects.get(email="new@example.com")
    assert user.auth_provider == "linkedtrust"
    # session login happened
    assert client.session.get("_auth_user_id") == str(user.pk)


@pytest.mark.django_db
def test_callback_rejects_state_mismatch(client, configured):
    _set_state(client, "expected")
    with patch("apps.accounts.oidc.exchange_code") as ex:
        resp = client.get(
            reverse("accounts:linkedtrust_callback"), {"code": "abc", "state": "forged"}
        )
    ex.assert_not_called()
    assert resp.status_code == 400
    assert User.objects.count() == 0


@pytest.mark.django_db
def test_google_callback_creates_user(client, configured):
    state = _set_state(client)
    claims = {
        "sub": "g-99",
        "email": "gmailer@example.com",
        "email_verified": True,
        "name": "G Person",
    }
    with patch("apps.accounts.google_oauth.resolve_identity", return_value=claims) as res:
        resp = client.get(reverse("accounts:google_callback"), {"code": "xyz", "state": state})
    res.assert_called_once()
    assert resp.status_code == 302
    user = User.objects.get(email="gmailer@example.com")
    assert user.auth_provider == "google"
    assert client.session.get("_auth_user_id") == str(user.pk)


@pytest.mark.django_db
def test_dev_login_disabled_returns_404(client, settings):
    settings.GOVKIT_DEV_LOGIN = False
    resp = client.get(reverse("accounts:dev_login"))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_dev_login_enabled_authenticates(client, settings):
    settings.GOVKIT_DEV_LOGIN = True
    User.objects.create_user(email="dev@example.com", password="pw12345!")
    resp = client.post(
        reverse("accounts:dev_login"),
        {"email": "dev@example.com", "password": "pw12345!"},
    )
    assert resp.status_code == 302
    assert client.session.get("_auth_user_id")
