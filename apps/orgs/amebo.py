"""
Report accepted memberships to amebo, the operational team registry.

amebo is the system-of-record for organizations / platform users / member tool
accounts (plan: 2026-07-16-workersvc-doorway-and-amebo-team-registry.md). When
someone accepts a GovKit invite, GovKit tells amebo about the membership so the
person gets provisioned across the team's tools:

    POST {AMEBO_BASE_URL}/api/orgs/provision
    Authorization: Bearer {AMEBO_S2S_TOKEN}

This reporter is strictly fire-and-forget. Provisioning failure must NEVER break
invite acceptance — amebo runs a reconcile loop that covers any drift — so every
exception is caught and logged as a warning. If AMEBO_BASE_URL or AMEBO_S2S_TOKEN
is unset the function is a no-op (debug log).

HTTP uses the standard library (``urllib``), like the repo's other outbound calls
(apps/accounts/http.py, apps/tasksources/adapters.py); tests mock
``urllib.request.urlopen`` — the true HTTP boundary.
"""

from __future__ import annotations

import json
import logging
import urllib.request

from django.conf import settings

from .models import MembershipRole

logger = logging.getLogger(__name__)

# Short timeout: this runs on the invite-accept request path (post-commit), so a slow
# amebo must not hold the user's page hostage.
TIMEOUT_SECONDS = 5

# GovKit's tool key in amebo's member_tool_accounts registry.
TOOL_KEY = "govkit"

# amebo's provision contract only knows "member" | "admin"; GovKit's finer-grained
# roles (steward) report as "member" — tool-level rights stay GovKit's own business.
_AMEBO_ADMIN = "admin"
_AMEBO_MEMBER = "member"


def _amebo_role(role: str) -> str:
    return _AMEBO_ADMIN if role == MembershipRole.ADMIN else _AMEBO_MEMBER


def provision_membership(org, user, role: str) -> None:
    """
    Report `user`'s membership in `org` (with GovKit role `role`) to amebo.

    No-op when AMEBO_BASE_URL / AMEBO_S2S_TOKEN are unset. Never raises: any
    failure is logged as a warning and swallowed (amebo reconciles drift).
    """
    base_url = settings.AMEBO_BASE_URL
    token = settings.AMEBO_S2S_TOKEN
    if not base_url or not token:
        logger.debug(
            "amebo provisioning skipped (AMEBO_BASE_URL/AMEBO_S2S_TOKEN unset): %s @ %s",
            user.pk,
            org.slug,
        )
        return

    try:
        # lt_sub is the LinkedTrust OIDC subject — only present when the user's
        # external identity IS LinkedTrust (explicit map, never inferred).
        lt_sub = user.auth_provider_id if user.auth_provider == "linkedtrust" else ""
        body = {
            "slug": org.slug,
            "name": org.display_name,
            "source": "govkit-accept",
            "members": [
                {
                    "email": user.email or None,
                    "lt_sub": lt_sub or None,
                    "display_name": user.display_name or None,
                    "role": _amebo_role(role),
                    "tool_accounts": [
                        {
                            "tool_key": TOOL_KEY,
                            "external_id": str(user.pk),
                            "username": None,
                        }
                    ],
                }
            ],
        }
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}/api/orgs/provision",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        with urllib.request.urlopen(  # nosec B310 - base URL from deploy env
            request, timeout=TIMEOUT_SECONDS
        ) as response:
            response.read()
        logger.info("amebo provisioned membership: user %s @ %s", user.pk, org.slug)
    except Exception:
        logger.warning(
            "amebo provisioning failed for user %s @ %s — acceptance unaffected, "
            "amebo's reconcile loop will catch up",
            user.pk,
            org.slug,
            exc_info=True,
        )
