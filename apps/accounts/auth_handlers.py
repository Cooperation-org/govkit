"""
Auth seam — the user upsert entry point for OIDC/OAuth logins.

`get_or_create_user(userinfo)` is named by settings.LINKEDTRUST_USER_HANDLER and is called
by the LinkedTrust OIDC callback (apps/accounts/views.py). Google sign-in reuses the same
upsert via `upsert_oauth_user`. Because GovKit logs users into a Django SESSION rather than
minting API tokens, these return the local `accounts.User` (not the upstream package's
`(user, tokens)` tuple).

Identity resolution order (explicit, never inferred — BOUNDARIES principle):
  1. Match on (auth_provider, auth_provider_id) — the stable OIDC subject.
  2. Else, if the provider asserts a VERIFIED email, match an existing user by email and
     link this provider identity to them (only if that user has no provider identity yet).
  3. Else create a new user.

An unverified email is never used to match or link an existing account.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.db import transaction

User = get_user_model()


class UserUpsertError(Exception):
    """The provider payload lacked what we need to identify or create a user."""


def _clean(value: Any) -> str:
    return (value or "").strip() if isinstance(value, str) else ""


def _display_name(userinfo: dict) -> str:
    return _clean(userinfo.get("name")) or _clean(userinfo.get("preferred_username"))


@transaction.atomic
def upsert_oauth_user(provider: str, userinfo: dict[str, Any]):
    """
    Find or create an accounts.User for an OAuth/OIDC identity.

    Args:
        provider: "linkedtrust" | "google".
        userinfo: OIDC claims, at least {"sub", "email"}; optionally "email_verified",
            "name"/"preferred_username", "picture".

    Returns:
        User: the resolved local user (never a token tuple — session login is used).
    """
    sub = _clean(userinfo.get("sub"))
    if not sub:
        raise UserUpsertError("Provider payload has no subject ('sub').")

    email = User.objects.normalize_email(_clean(userinfo.get("email")))
    email_verified = bool(userinfo.get("email_verified"))
    display_name = _display_name(userinfo)
    avatar_url = _clean(userinfo.get("picture"))

    # 1. Match on the stable provider subject.
    user = User.objects.filter(auth_provider=provider, auth_provider_id=sub).first()
    if user is not None:
        return _refresh_profile(user, display_name, avatar_url)

    # 2. Link to an existing account by VERIFIED email (only if unclaimed by a provider).
    if email and email_verified:
        existing = User.objects.filter(email__iexact=email).first()
        if existing is not None:
            if existing.auth_provider_id:
                # Already claimed by a (different) provider identity — refresh, don't reassign.
                return _refresh_profile(existing, display_name, avatar_url)
            # M2: refuse to auto-take-over a privileged or password-protected account via a
            # freshly-asserted provider identity. A verified email alone must not grant control
            # of a staff/superuser account or one that already has a real login credential —
            # otherwise an attacker who registers the same email at an IdP inherits the account.
            # The owner must sign in with their existing credential and link the provider
            # explicitly.
            if existing.is_staff or existing.is_superuser or existing.has_usable_password():
                raise UserUpsertError(
                    "An account with this email already exists. Sign in with your existing "
                    "credentials and link this provider from your account settings."
                )
            # Provider-less, password-less placeholder (e.g. invited-but-never-logged-in):
            # safe to claim by verified email.
            existing.auth_provider = provider
            existing.auth_provider_id = sub
            return _refresh_profile(existing, display_name, avatar_url)

    # 3. Create a new user. Email is required (it is the unique username field).
    if not email:
        raise UserUpsertError("Cannot create a user without an email address.")
    # If the email is taken but we reached here, the provider did NOT assert it as verified
    # (a verified match would have linked above). Refuse rather than hijack or duplicate.
    if User.objects.filter(email__iexact=email).exists():
        raise UserUpsertError(
            "An account with this email exists but the provider did not verify ownership."
        )
    user = User.objects.create_user(
        email=email,
        password=None,  # OAuth/OIDC users have no usable password
        display_name=display_name,
        avatar_url=avatar_url,
        auth_provider=provider,
        auth_provider_id=sub,
    )
    return user


def _refresh_profile(user, display_name: str, avatar_url: str):
    """Keep display_name / avatar fresh from the provider without clobbering with blanks."""
    if display_name and user.display_name != display_name:
        user.display_name = display_name
    if avatar_url and user.avatar_url != avatar_url:
        user.avatar_url = avatar_url
    # auth_provider / auth_provider_id may have just been set when linking by email.
    user.save(update_fields=["display_name", "avatar_url", "auth_provider", "auth_provider_id"])
    return user


def get_or_create_user(userinfo: dict[str, Any]):
    """
    LinkedTrust OIDC user handler (settings.LINKEDTRUST_USER_HANDLER).

    Upserts on the OIDC subject, falling back to a verified-email match. Returns the local
    accounts.User; the caller logs it into a Django session.
    """
    return upsert_oauth_user("linkedtrust", userinfo)
