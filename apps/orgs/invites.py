"""
Org invites as stateless, signed capability tokens.

The frozen schema has no Invite table, and adding one is out of scope for feature agents.
Instead an invite is a signed, expiring token (django.core.signing) that encodes the
target org, the role to grant, the invited email, and an optional per-member hourly rate.
The admin shares the resulting link; whoever logs in through it (LinkedTrust, Google, or
the dev seam) gets a Membership created for their authenticated user.

This keeps invites tamper-proof and self-expiring with zero new persistence. The token is
a bearer capability: possession of a valid, unexpired link is authorization to join. The
invited email travels in the payload for display/audit, not as a hard gate, because an
OAuth identity may legitimately carry a different verified email than the one invited.

Session flow: an anonymous visitor hitting an invite link has the raw token stashed in
their session and is sent to log in; `consume_pending_invite` runs right after login and
materializes the Membership.
"""

from __future__ import annotations

from django.core import signing

from .models import Membership, MembershipRole, Org

INVITE_SALT = "govkit.orgs.invite"
INVITE_MAX_AGE_SECONDS = 14 * 24 * 60 * 60  # 14 days
SESSION_KEY = "pending_invite_token"


class InviteError(Exception):
    """The invite token is malformed, tampered with, or expired."""


def make_invite_token(org: Org, role: str, email: str = "", hourly_rate=None) -> str:
    """Create a signed invite token for an org. `hourly_rate` may be None (use org default)."""
    if role not in MembershipRole.values:
        raise InviteError(f"Unknown role: {role}")
    payload = {
        "org": org.id,
        "role": role,
        "email": (email or "").strip().lower(),
        "rate": str(hourly_rate) if hourly_rate is not None else None,
    }
    return signing.dumps(payload, salt=INVITE_SALT)


def read_invite_token(token: str) -> dict:
    """Verify + decode an invite token. Raises InviteError if invalid or expired."""
    try:
        payload = signing.loads(token, salt=INVITE_SALT, max_age=INVITE_MAX_AGE_SECONDS)
    except signing.SignatureExpired as exc:
        raise InviteError("This invite has expired.") from exc
    except signing.BadSignature as exc:
        raise InviteError("This invite link is invalid.") from exc
    if not isinstance(payload, dict) or "org" not in payload or "role" not in payload:
        raise InviteError("This invite link is malformed.")
    return payload


def accept_invite_for_user(token: str, user) -> Membership:
    """
    Materialize (or return the existing) Membership for `user` from an invite token.

    Idempotent: re-accepting an invite the user already fulfilled returns their existing
    membership unchanged rather than duplicating or overwriting their role.
    """
    payload = read_invite_token(token)
    org = Org.objects.filter(id=payload["org"]).first()
    if org is None:
        raise InviteError("The organization for this invite no longer exists.")

    existing = Membership.objects.filter(org=org, user=user).first()
    if existing is not None:
        return existing

    rate = payload.get("rate")
    return Membership.objects.create(
        org=org,
        user=user,
        role=payload["role"],
        hourly_rate=rate,  # DecimalField accepts a numeric string or None
    )


def consume_pending_invite(request):
    """
    If the session holds a pending invite token, create the Membership for the now
    logged-in user and clear it. Returns the joined Org (for redirect) or None.
    """
    token = request.session.pop(SESSION_KEY, None)
    if not token or not request.user.is_authenticated:
        return None
    try:
        membership = accept_invite_for_user(token, request.user)
    except InviteError:
        return None
    return membership.org
