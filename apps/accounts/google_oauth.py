"""
Google OAuth — server-side authorization-code flow (secondary login).

Independent self-hosters who do not run a LinkedTrust IdP can offer Google sign-in
instead. This uses the OAuth 2.0 / OpenID Connect *authorization code* flow (the recipe's
`code` variant, see /opt/shared/cobox/oauth-login-pattern.md and scratch Q4):

    authorize_url(redirect_uri, state)   -> str    (browser redirect target)
    exchange_code(code, redirect_uri)    -> dict    (token response incl. id_token)
    claims_from_id_token(id_token)       -> dict    (validated OIDC claims)
    resolve_identity(code, redirect_uri) -> dict    (the two chained + validated)

ID-token signature note: the id_token is obtained directly from Google's token endpoint
over TLS in a server-to-server request authenticated with the client secret. Per OpenID
Connect Core §3.1.3.7, signature validation MAY be skipped when the token is received
directly from the token endpoint over a TLS channel the client trusts. We still validate
the security-relevant claims (iss, aud, exp, email_verified). No JWKS round-trip needed,
so no extra dependency.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from urllib.parse import urlencode

from django.conf import settings

from .http import HttpError, post_form

logger = logging.getLogger(__name__)

AUTHORIZE_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # nosec B105 - public URL, not a secret
VALID_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
SCOPES = "openid email profile"


class GoogleOAuthError(Exception):
    """Any failure in the Google OAuth flow."""


def _client_id() -> str:
    client_id = settings.GOOGLE_OAUTH_CLIENT_ID
    if not client_id:
        raise GoogleOAuthError("GOOGLE_OAUTH_CLIENT_ID is not configured")
    return client_id


def authorize_url(redirect_uri: str, state: str) -> str:
    """Build the Google authorize URL for a browser redirect."""
    params = urlencode(
        {
            "response_type": "code",
            "client_id": _client_id(),
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "state": state,
            "access_type": "online",
            "prompt": "select_account",
        }
    )
    return f"{AUTHORIZE_ENDPOINT}?{params}"


def exchange_code(code: str, redirect_uri: str) -> dict:
    """Exchange an authorization code for tokens at Google's token endpoint."""
    try:
        return post_form(
            TOKEN_ENDPOINT,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": _client_id(),
                "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            },
        )
    except HttpError as exc:
        logger.error("Google token exchange failed: %s", exc)
        raise GoogleOAuthError("Token exchange failed") from exc
    except json.JSONDecodeError as exc:
        raise GoogleOAuthError("Malformed token response") from exc


def _decode_segment(segment: str) -> dict:
    padding = "=" * (-len(segment) % 4)
    raw = base64.urlsafe_b64decode(segment + padding)
    return json.loads(raw)


def claims_from_id_token(id_token: str) -> dict:
    """
    Decode and validate a Google ID token's claims.

    Validates issuer, audience (our client id), expiry, and email verification. Raises
    GoogleOAuthError on any failure. Returns the claims dict on success.
    """
    parts = id_token.split(".")
    if len(parts) != 3:
        raise GoogleOAuthError("Malformed id_token")
    try:
        claims = _decode_segment(parts[1])
    except (ValueError, json.JSONDecodeError) as exc:
        raise GoogleOAuthError("Unreadable id_token payload") from exc

    if claims.get("iss") not in VALID_ISSUERS:
        raise GoogleOAuthError("Unexpected id_token issuer")
    if claims.get("aud") != _client_id():
        raise GoogleOAuthError("id_token audience mismatch")
    exp = claims.get("exp")
    if not exp or int(exp) < int(time.time()):
        raise GoogleOAuthError("id_token has expired")
    if not claims.get("email"):
        raise GoogleOAuthError("id_token carries no email")
    if not claims.get("email_verified"):
        raise GoogleOAuthError("Google email is not verified")
    return claims


def resolve_identity(code: str, redirect_uri: str) -> dict:
    """Full flow: exchange the code, then validate the returned id_token's claims."""
    token_response = exchange_code(code, redirect_uri)
    id_token = token_response.get("id_token")
    if not id_token:
        raise GoogleOAuthError("Token response had no id_token")
    return claims_from_id_token(id_token)
