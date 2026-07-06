"""
Auth seam — the user upsert entry point for OIDC/OAuth logins.

`django-linkedtrust-auth` calls the function named by settings.LINKEDTRUST_USER_HANDLER
with the OIDC `userinfo` dict and expects `(user, tokens_dict)` back. This stub gives the
auth agent a single, well-named place to implement that mapping. It intentionally raises
so no half-wired flow silently "works".

Auth agent: implement get_or_create_user() to find/create an accounts.User keyed on
(auth_provider, auth_provider_id) — falling back to email — and to set display_name /
avatar_url from userinfo. See apps/accounts/README stub and the reference package at
django-linkedtrust-auth for the contract.
"""

from __future__ import annotations

from typing import Any


def get_or_create_user(userinfo: dict[str, Any]):  # pragma: no cover - stub
    """
    Map an OIDC userinfo payload to a local accounts.User.

    Args:
        userinfo: OIDC claims, e.g. {"email", "name", "preferred_username", "sub", ...}.

    Returns:
        tuple[User, dict]: the local user and an app-token dict for the frontend.
    """
    raise NotImplementedError(
        "Auth agent: implement OIDC user upsert here "
        "(key on auth_provider/auth_provider_id, fall back to email)."
    )
