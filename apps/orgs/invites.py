"""
Org invites as stateful, single-use records addressed by an opaque code (magic link).

An Invite row (apps/orgs/models.Invite) carries who the invite is for, the inviter's
drafted words, a lifecycle status (created → committed → accepted, or revoked), and an
expiry. The code in the link is a bearer capability: possession of a live code is
authorization to join. The invited email is display/audit only, not a hard gate, because
an OAuth identity may legitimately carry a different verified email than the one invited.

This replaces the earlier stateless signed-token scheme (clean cutover, no dual system):
Golda's two-step doorway flow needs state — single-use codes, revocation, the committed
claim id, and visible "committed but never logged in" status — which a signed blob
cannot carry.

Session flow: an anonymous visitor hitting an accept link has the CODE stashed in their
session and is sent to log in; `consume_pending_invite` runs right after login and
materializes the Membership.
"""

from __future__ import annotations

from .models import Invite, Membership

SESSION_KEY = "pending_invite_code"


class InviteError(Exception):
    """The invite code is unknown, expired, revoked, or already used."""


def get_invite_for_accept(code: str) -> Invite:
    """Look up a live invite by code. Raises InviteError unless it can still be accepted."""
    invite = Invite.objects.filter(code=code).select_related("org").first()
    if invite is None:
        raise InviteError("This invite link is invalid.")
    if not invite.can_accept:
        raise InviteError("This invite is no longer active.")
    return invite


def accept_invite_for_user(invite: Invite, user) -> Membership:
    """
    Materialize (or return the existing) Membership for `user` from a live invite,
    and mark the invite accepted (single-use: accepted invites are dead).

    Idempotent for the user: if they already belong to the org, their existing
    membership is returned unchanged rather than duplicated or re-roled.
    """
    if not invite.can_accept:
        raise InviteError("This invite is no longer active.")

    membership = Membership.objects.filter(org=invite.org, user=user).first()
    if membership is None:
        membership = Membership.objects.create(org=invite.org, user=user, role=invite.role)
    invite.mark_accepted()
    return membership


def consume_pending_invite(request):
    """
    If the session holds a pending invite code, create the Membership for the now
    logged-in user and clear it. Returns the joined Org (for redirect) or None.
    """
    code = request.session.pop(SESSION_KEY, None)
    if not code or not request.user.is_authenticated:
        return None
    try:
        invite = get_invite_for_accept(code)
        membership = accept_invite_for_user(invite, request.user)
    except InviteError:
        return None
    return membership.org
