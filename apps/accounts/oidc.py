"""
LinkedTrust OIDC — server-side authorization-code flow (vendored, session-based).

Adapted from Cooperation-org/django-linkedtrust-auth `oidc.py`. The upstream package
redirects to a separate frontend with tokens in the URL fragment; GovKit is a
server-rendered Django app, so our views log the resolved user into a Django SESSION
instead (see apps/accounts/views.py). Only the three IdP operations live here:

    authorize_url(redirect_uri, state)  -> str   (browser redirect target)
    exchange_code(code, redirect_uri)   -> dict  (token response)
    get_userinfo(access_token)          -> dict  (OIDC claims)

HTTP uses the standard library (urllib) so the package pulls in no extra dependency and
is trivially mockable in tests. Config is read from Django settings (LINKEDTRUST_*).
"""

from __future__ import annotations

import json
import logging
from urllib.parse import urlencode

from django.conf import settings

from .http import HttpError, get_json, post_form

logger = logging.getLogger(__name__)


class OIDCError(Exception):
    """Any failure talking to the LinkedTrust IdP."""


def _issuer() -> str:
    issuer = (settings.LINKEDTRUST_URL or "").rstrip("/")
    if not issuer:
        raise OIDCError("LINKEDTRUST_URL is not configured")
    return issuer


def authorize_url(redirect_uri: str, state: str) -> str:
    """Build the IdP authorize URL for a browser redirect."""
    client_id = settings.LINKEDTRUST_CLIENT_ID
    if not client_id:
        raise OIDCError("LINKEDTRUST_CLIENT_ID is not configured")
    params = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": settings.LINKEDTRUST_SCOPES,
            "state": state,
        }
    )
    return f"{_issuer()}/oauth/authorize?{params}"


def exchange_code(code: str, redirect_uri: str) -> dict:
    """Exchange an authorization code for tokens at the IdP token endpoint."""
    try:
        return post_form(
            f"{_issuer()}/oauth/token",
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": settings.LINKEDTRUST_CLIENT_ID,
                "client_secret": settings.LINKEDTRUST_CLIENT_SECRET,
            },
        )
    except HttpError as exc:
        logger.error("LinkedTrust token exchange failed: %s", exc)
        raise OIDCError("Token exchange failed") from exc
    except json.JSONDecodeError as exc:
        raise OIDCError("Malformed token response") from exc


def get_userinfo(access_token: str) -> dict:
    """Fetch OIDC claims from the IdP userinfo endpoint."""
    try:
        return get_json(
            f"{_issuer()}/oauth/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    except HttpError as exc:
        logger.error("LinkedTrust userinfo failed: %s", exc)
        raise OIDCError("Failed to fetch user profile") from exc
    except json.JSONDecodeError as exc:
        raise OIDCError("Malformed userinfo response") from exc
