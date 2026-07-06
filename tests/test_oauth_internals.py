"""Unit tests for the Google id-token validator and the stdlib HTTP helpers (mocked)."""

import base64
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from apps.accounts import google_oauth, http


def _make_id_token(**claims):
    def seg(obj):
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{seg({'alg': 'RS256'})}.{seg(claims)}.signature"


@pytest.fixture
def google_configured(settings):
    settings.GOOGLE_OAUTH_CLIENT_ID = "aud-123"
    return settings


def _valid_claims(**over):
    base = {
        "iss": "https://accounts.google.com",
        "aud": "aud-123",
        "sub": "g-sub",
        "email": "u@example.com",
        "email_verified": True,
        "exp": int(time.time()) + 600,
    }
    base.update(over)
    return base


@pytest.mark.django_db
def test_valid_id_token_claims(google_configured):
    claims = google_oauth.claims_from_id_token(_make_id_token(**_valid_claims()))
    assert claims["sub"] == "g-sub"
    assert claims["email"] == "u@example.com"


@pytest.mark.django_db
def test_rejects_wrong_audience(google_configured):
    with pytest.raises(google_oauth.GoogleOAuthError):
        google_oauth.claims_from_id_token(_make_id_token(**_valid_claims(aud="someone-else")))


@pytest.mark.django_db
def test_rejects_expired_token(google_configured):
    with pytest.raises(google_oauth.GoogleOAuthError):
        google_oauth.claims_from_id_token(
            _make_id_token(**_valid_claims(exp=int(time.time()) - 10))
        )


@pytest.mark.django_db
def test_rejects_unverified_email(google_configured):
    with pytest.raises(google_oauth.GoogleOAuthError):
        google_oauth.claims_from_id_token(_make_id_token(**_valid_claims(email_verified=False)))


@pytest.mark.django_db
def test_rejects_bad_issuer(google_configured):
    with pytest.raises(google_oauth.GoogleOAuthError):
        google_oauth.claims_from_id_token(_make_id_token(**_valid_claims(iss="evil.example")))


@pytest.mark.django_db
def test_resolve_identity_chains_exchange_and_validate(google_configured):
    id_token = _make_id_token(**_valid_claims())
    with patch.object(google_oauth, "exchange_code", return_value={"id_token": id_token}):
        claims = google_oauth.resolve_identity("code", "https://app/callback")
    assert claims["sub"] == "g-sub"


def test_http_post_form_parses_json():
    fake = MagicMock()
    fake.read.return_value = b'{"access_token": "tok"}'
    fake.__enter__.return_value = fake
    fake.__exit__.return_value = False
    with patch("apps.accounts.http.urlopen", return_value=fake) as uo:
        result = http.post_form("https://idp/token", {"code": "c", "skip": None})
    assert result == {"access_token": "tok"}
    # None-valued form fields are dropped from the body.
    sent = uo.call_args.args[0]
    assert b"skip" not in sent.data


def test_http_raises_on_error_status():
    from urllib.error import HTTPError

    err = HTTPError("https://idp/token", 400, "Bad Request", {}, None)
    with patch("apps.accounts.http.urlopen", side_effect=err):
        with pytest.raises(http.HttpError):
            http.get_json("https://idp/userinfo")
