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

from django.conf import settings
from django.db import transaction

from .amebo import provision_membership
from .genesis import start_genesis
from .slugs import normalize_org_slug, unique_org_slug
from .models import (
    Invite,
    InviteAudience,
    InviteKind,
    InviteStatus,
    Membership,
    MembershipRole,
    Org,
    ValuationConfig,
)

SESSION_KEY = "pending_invite_code"


def cohort_front_door_url(org):
    """The cohort dash landing for a freshly joined member of `org`, or None.

    On a cohort deployment (settings.COHORT_FRONT_DOOR, validated at startup) the dash
    on the workers.vc apex is THE front door — GovKit's own dashboard is a menu item
    there — so every path that completes an invite join redirects here instead of
    orgs:dashboard. Unset (the default) returns None: callers keep today's behavior.
    """
    template = settings.COHORT_FRONT_DOOR
    return template.format(org_slug=org.slug) if template else None


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


def create_venture_org(invite: Invite, user) -> Org:
    """
    A founder's venture becomes a real org the moment they accept: Org (default
    valuation config, unit "slices") + the founder as admin + the seeded module
    checklist. Their first hour starts here, not in a setup form.

    The pie starts empty. What the venture is worth on day one, and whether a sponsor
    holds part of it, is the founder's own call — offered to them on their pie board
    once they are in, never decided for them by whoever sent the invite.
    """
    base = normalize_org_slug(invite.venture_name) or f"venture-{invite.code[:8].lower()}"
    org = Org.objects.create(
        slug=unique_org_slug(base),
        display_name=invite.venture_name,
        unit_name="slices",
    )
    ValuationConfig.objects.create(org=org)
    Membership.objects.create(org=org, user=user, role=MembershipRole.ADMIN)
    start_genesis(org)
    return org


@transaction.atomic
def accept_invite_for_user(invite: Invite, user) -> tuple[Membership | None, Org | None]:
    """
    Materialize (or return the existing) Membership for `user` from a live invite,
    and mark the invite accepted (single-use: accepted invites are dead). Founder
    invites naming a venture also create the venture org (returned second, and the
    right landing page for the founder).

    POOL invites (InviteKind.POOL) never create a Membership: accepting one records
    the person in the applicant pool (the accepted invite row, accepted_by set) and
    returns (None, None). No membership, no slices, no org — orgs are never
    auto-created for pool people; they come only from a deliberate founder invite
    naming a real venture, or an operator/kickoff add-team run (Golda, 2026-07-20).

    Idempotent for the user: if they already belong to the org, their existing
    membership is returned unchanged — and the invite is NOT consumed (an existing
    member on an invite link is previewing it, not joining).
    """
    if not invite.can_accept:
        raise InviteError("This invite is no longer active.")

    membership = Membership.objects.filter(org=invite.org, user=user).first()
    if membership is not None:
        # An existing member touching an invite link is previewing it (typically the
        # inviter checking their own mint) — never burn the single-use code on them,
        # never re-role them, never spawn the venture. The invite stays live for the
        # person it was minted for.
        return membership, None
    if invite.kind == InviteKind.POOL:
        invite.mark_accepted(by=user)
        return None, None
    if invite.kind == InviteKind.BYOV:
        # Founder Bringing their Own Venture: create THAT org (invitee as admin)
        # and land them on it. They do NOT join the inviting org — the venture is
        # its own home. (The distinct third invite type, golda 2026-07-24.)
        venture_org = create_venture_org(invite, user)
        invite.mark_accepted(by=user)
        transaction.on_commit(lambda: provision_membership(venture_org, user, MembershipRole.ADMIN))
        return None, venture_org
    if invite.audience == InviteAudience.SUPPORTER:
        # Supporters never join an org (golda 2026-07-22): they are the email
        # list — wall card + dash + contact capture, no membership, no slices,
        # and NOT listed in the applicant pool (that's people seeking a team).
        invite.mark_accepted(by=user)
        return None, None
    # Org membership: join the inviting org (a founder invited here is a co-founder
    # of THIS org, no venture — venture belongs to the BYOV path above).
    membership = Membership.objects.create(org=invite.org, user=user, role=invite.role)
    invite.mark_accepted(by=user)

    # Report the membership to amebo (the operational team registry) so the person
    # gets provisioned across the team's tools. After commit, outside the transaction
    # (L7: network I/O never holds a DB transaction open). provision_membership never raises.
    org, role = invite.org, membership.role  # the role the membership actually got
    transaction.on_commit(lambda: provision_membership(org, user, role))
    return membership, None


def consume_pending_invite(request):
    """
    If the session holds a pending invite code, complete the accept for the now
    logged-in user and clear it. Returns the URL to land on — the cohort dash
    (front door / pool landing) whenever configured, GovKit's own pages only on
    standalone deployments — or None when no invite was pending. On a cohort
    deployment every invite path sends people to the dash, never a GovKit page
    (golda, 2026-07-21).
    """
    code = request.session.pop(SESSION_KEY, None)
    if not code or not request.user.is_authenticated:
        return None
    try:
        invite = get_invite_for_accept(code)
        membership, venture_org = accept_invite_for_user(invite, request.user)
    except InviteError:
        return None
    return landing_after_accept(membership, venture_org)


def landing_after_accept(membership, venture_org):
    """Where an accepted invite lands the person."""
    from django.shortcuts import resolve_url

    if membership is None and venture_org is None:
        # Pool/supporter accept: no org joined AND no venture created, so nothing
        # to land on. A BYOV accept returns (None, venture_org) — membership None
        # but the founder DID get a venture; it must fall through to the front
        # door below, not the pool landing (same fix as _finish_accept — this
        # path is the one founders hit when forced through LinkedTrust login).
        return settings.COHORT_POOL_LANDING or resolve_url("orgs:landing")
    org = venture_org or membership.org
    return cohort_front_door_url(org) or resolve_url("orgs:dashboard", org_slug=org.slug)


def claim_invite_for_email(request):
    """A live invite addressed to the person who just signed in admits them.

    Signing in and being invited used to be two separate doors, and only the invite
    link opened one: the code was the sole key. Someone who was invited but reached
    sign-in another way (never got the link, used a bookmark, typed workers.vc) landed
    on an empty org picker with no way to say who they were and no sign anything was
    wrong. "If i approve someone they should be IN ... if they signin from workers.vc
    signin link and i accepted them it should be IN just ON DASH" (golda, 2026-08-08).

    Deliberately narrow, so nothing that works today changes:
      - only for someone who belongs to no org yet, so an existing member's further
        invites still travel by their own link;
      - never BYOV — a venture org is minted when a founder brings a venture, never
        as a side effect of a login (golda, 2026-07-20);
      - matched on the email the identity provider just verified, and only against
        invites that are still live.

    Returns the URL to land on, or None when there was nothing to claim.
    """
    user = request.user
    if not user.is_authenticated or not user.email:
        return None
    if Membership.objects.filter(user=user).exists():
        return None
    invite = (
        Invite.objects.filter(
            email__iexact=user.email,
            status__in=(InviteStatus.CREATED, InviteStatus.COMMITTED),
        )
        .exclude(kind=InviteKind.BYOV)
        .select_related("org")
        .order_by("-created_at")
        .first()
    )
    if invite is None or not invite.can_accept:
        return None
    try:
        membership, venture_org = accept_invite_for_user(invite, user)
    except InviteError:
        return None
    return landing_after_accept(membership, venture_org)
