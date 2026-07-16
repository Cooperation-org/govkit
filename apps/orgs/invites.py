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

from django.db import transaction
from django.utils.text import slugify

from .amebo import provision_membership
from .genesis import seed_genesis
from .models import (
    Invite,
    InviteAudience,
    Membership,
    MembershipRole,
    Org,
    ValuationConfig,
)

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


def _unique_org_slug(base: str) -> str:
    slug = base
    n = 2
    while Org.objects.filter(slug=slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


def create_venture_org(invite: Invite, user) -> Org:
    """
    A founder's venture becomes a real org the moment they accept: Org (default
    valuation config, unit "slices") + the founder as admin + the seeded module
    checklist. Their first hour starts here, not in a setup form.
    """
    base = slugify(invite.venture_name)[:60] or f"venture-{invite.code[:8].lower()}"
    org = Org.objects.create(
        slug=_unique_org_slug(base),
        display_name=invite.venture_name,
        unit_name="slices",
    )
    ValuationConfig.objects.create(org=org)
    Membership.objects.create(org=org, user=user, role=MembershipRole.ADMIN)
    seed_genesis(org)
    return org


@transaction.atomic
def accept_invite_for_user(invite: Invite, user) -> tuple[Membership, Org | None]:
    """
    Materialize (or return the existing) Membership for `user` from a live invite,
    and mark the invite accepted (single-use: accepted invites are dead). Founder
    invites naming a venture also create the venture org (returned second, and the
    right landing page for the founder).

    Idempotent for the user: if they already belong to the org, their existing
    membership is returned unchanged rather than duplicated or re-roled.
    """
    if not invite.can_accept:
        raise InviteError("This invite is no longer active.")

    membership = Membership.objects.filter(org=invite.org, user=user).first()
    if membership is None:
        membership = Membership.objects.create(org=invite.org, user=user, role=invite.role)
    venture_org = None
    if invite.audience == InviteAudience.FOUNDER and invite.venture_name:
        venture_org = create_venture_org(invite, user)
    invite.mark_accepted()

    # Report the membership(s) to amebo (the operational team registry) so the person
    # gets provisioned across the team's tools. After commit, outside the transaction
    # (L7: network I/O never holds a DB transaction open), and only for memberships
    # that actually exist. provision_membership never raises.
    org, role = invite.org, membership.role  # the role the membership actually got
    transaction.on_commit(lambda: provision_membership(org, user, role))
    if venture_org is not None:
        # The founder's freshly created venture org, founder as admin.
        transaction.on_commit(lambda: provision_membership(venture_org, user, MembershipRole.ADMIN))
    return membership, venture_org


def consume_pending_invite(request):
    """
    If the session holds a pending invite code, create the Membership for the now
    logged-in user and clear it. Returns the Org to land on (the founder's new
    venture org when one was created, else the joined org) or None.
    """
    code = request.session.pop(SESSION_KEY, None)
    if not code or not request.user.is_authenticated:
        return None
    try:
        invite = get_invite_for_accept(code)
        membership, venture_org = accept_invite_for_user(invite, request.user)
    except InviteError:
        return None
    return venture_org or membership.org
