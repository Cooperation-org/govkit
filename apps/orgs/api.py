"""
DRF API for orgs (API-first mirror of the onboarding + members UI).

Because these routes are mounted flatly at /api/v1/orgs/ (no org_slug in the path),
OrgContextMiddleware does not set request.org here. Scoping is instead enforced per
viewset: querysets are limited to orgs the requesting user belongs to (superusers see
all), and mutating member/invite/rate actions require the caller to be an ADMIN of the
target org.

Endpoints:
  GET  /api/v1/orgs/orgs/                       orgs the caller belongs to
  POST /api/v1/orgs/orgs/                       onboarding: create org + config + admin
  GET  /api/v1/orgs/orgs/{slug}/                org detail (+ valuation config)
  GET  /api/v1/orgs/orgs/{slug}/members/        members of the org
  POST /api/v1/orgs/orgs/{slug}/invite/         admin: mint a magic-link invite
  POST /api/v1/orgs/orgs/{slug}/rate/           admin: set org-wide default rate
  PATCH /api/v1/orgs/memberships/{id}/          admin: set a member's role / rate override

Org-scoped (path carries org_slug, so OrgContextMiddleware resolves request.org and
enforces membership — 404 unknown org, 403 authenticated non-member):
  GET  /api/v1/orgs/{org_slug}/checklist/       genesis checklist as JSON (read-only)

Doorway S2S (plain Django views, Bearer settings.GOVKIT_S2S_TOKEN — NOT session auth;
the magic-link contract on the coordination board):
  GET  /api/v1/orgs/{org_slug}/invites/{code}/            invite detail for the doorway
  POST /api/v1/orgs/{org_slug}/invites/{code}/committed/  doorway posts the claim id back
  POST /api/v1/orgs/{org_slug}/invites/mint/    doorway mints an invite for an existing card
  GET  /api/v1/orgs/{org_slug}/invite-links/{code}/  what a shared link opens, and if it is open
  GET  /api/v1/orgs/ventures/public/                      every venture's public card
  GET  /api/v1/orgs/people/{claim_id}/contact/            how a venture reaches one person
  GET  /api/v1/orgs/{org_slug}/members/by-discord/{id}/   who a Discord user is here
  GET  /api/v1/orgs/{org_slug}/profile/                   one team's public profile
  PATCH /api/v1/orgs/{org_slug}/profile/write/            set tagline/pitch/site/calendar...
  GET  /api/v1/orgs/{org_slug}/profile/{kind}/            list pictures|links|posts|quotes
  POST /api/v1/orgs/{org_slug}/profile/{kind}/            add one row to that list
  DELETE /api/v1/orgs/{org_slug}/profile/{kind}/{id}/     remove one row
  POST /api/v1/orgs/{org_slug}/profile/upload/            a file in, a URL back

Everything an admin can do to a team's PUBLIC page on the settings screen is on
that last group of endpoints too (golda 2026-07-28: "i want everything to be
agenticable"). Members, rates, pie and governance are deliberately not.
"""

from __future__ import annotations

import json
from datetime import date

import logging

from django.conf import settings
from django.db.models import Count
from django.http import Http404, JsonResponse
from django.urls import path, reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter
from rest_framework.views import APIView

from apps.commons import storage

from .embed_auth import EmbedSessionAuthentication
from .genesis import modules_for, serialize_modules, toggle_item
from .models import (
    Invite,
    InviteAudience,
    InviteKind,
    InviteLink,
    InviteStatus,
    Membership,
    MembershipRole,
    Org,
    OrgLink,
    OrgPicture,
    OrgPost,
    OrgQuote,
)
from .serializers import (
    InviteSerializer,
    MembershipSerializer,
    OnboardingSerializer,
    OrgRateSerializer,
    OrgSerializer,
)

logger = logging.getLogger(__name__)


def _is_admin(user, org) -> bool:
    if user.is_superuser:
        return True
    membership = Membership.objects.filter(org=org, user=user).first()
    return membership is not None and membership.role == MembershipRole.ADMIN


def _require_admin(user, org):
    if not _is_admin(user, org):
        raise PermissionDenied("Only organization admins may perform this action.")


class OrgViewSet(viewsets.ModelViewSet):
    """List/create orgs the caller belongs to; manage members via detail actions."""

    lookup_field = "slug"
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Org.objects.all()
        return Org.objects.filter(memberships__user=user).distinct()

    def get_serializer_class(self):
        if self.action == "create":
            return OnboardingSerializer
        return OrgSerializer

    @action(detail=True, methods=["get"])
    def members(self, request, slug=None):
        org = self.get_object()
        qs = Membership.objects.filter(org=org).select_related("user").order_by("user__email")
        return Response(MembershipSerializer(qs, many=True, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def invite(self, request, slug=None):
        org = self.get_object()
        _require_admin(request.user, org)
        serializer = InviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        doorway = data.pop("doorway")
        invite = Invite.objects.create(org=org, created_by=request.user, **data)
        if doorway and settings.DOORWAY_BASE_URL:
            link = f"{settings.DOORWAY_BASE_URL}{invite.code}/"
        else:
            link = request.build_absolute_uri(
                reverse("orgs:accept_invite", kwargs={"code": invite.code})
            )
        return Response(
            {"invite_link": link, "code": invite.code, "status": invite.status},
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def rate(self, request, slug=None):
        org = self.get_object()
        _require_admin(request.user, org)
        serializer = OrgRateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        org.default_hourly_rate = serializer.validated_data.get("default_hourly_rate")
        org.save(update_fields=["default_hourly_rate"])
        return Response(OrgSerializer(org, context={"request": request}).data)


class MembershipViewSet(
    mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet
):
    """Admin-only role / rate management for a single membership."""

    serializer_class = MembershipSerializer
    http_method_names = ["get", "put", "patch", "head", "options"]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Membership.objects.select_related("org", "user")
        return (
            Membership.objects.filter(org__memberships__user=user)
            .select_related("org", "user")
            .distinct()
        )

    def perform_update(self, serializer):
        membership = serializer.instance
        _require_admin(self.request.user, membership.org)
        new_role = serializer.validated_data.get("role", membership.role)
        if (
            membership.role == MembershipRole.ADMIN
            and new_role != MembershipRole.ADMIN
            and not Membership.objects.filter(org=membership.org, role=MembershipRole.ADMIN)
            .exclude(id=membership.id)
            .exists()
        ):
            raise PermissionDenied("An organization must keep at least one admin.")
        serializer.save()


# --- Genesis checklist (read-only JSON for the cohort dash) ------------------------------


class ChecklistView(APIView):
    """The org's genesis checklist as JSON (PLAN-cohort-dash.md item 2).

    Membership is enforced by OrgContextMiddleware via the org_slug kwarg. An org
    with no checklist (not a venture org) returns {"org_slug": ..., "modules": []}.
    Toggling happens at the sibling toggle endpoint — the curriculum is worked
    directly on the dash (Golda 2026-07-19).
    """

    def get(self, request, org_slug):
        return Response(
            {"org_slug": request.org.slug, "modules": serialize_modules(modules_for(request.org))}
        )


class ChecklistToggleView(APIView):
    """POST: flip one genesis item, exactly like the HTML dashboard toggle — any
    member; records who and when. Returns the item + its module's new count."""

    authentication_classes = [EmbedSessionAuthentication]

    def post(self, request, org_slug, item_key):
        if request.membership is None and not request.user.is_superuser:
            raise PermissionDenied("Only members may work the checklist.")
        done, module = toggle_item(request.org, item_key, request.user)
        if done is None:
            return Response({"error": "no such item"}, status=status.HTTP_404_NOT_FOUND)
        return Response(
            {
                "key": item_key,
                "done": done,
                "module": (
                    {"key": module["key"], "done": module["done"], "total": module["total"]}
                    if module
                    else None
                ),
            }
        )


# --- Doorway S2S invite endpoints --------------------------------------------------------
#
# Plain Django views on purpose: the caller is the doorway SERVER (shared bearer secret),
# not a browser session, so DRF's session-auth defaults don't apply. An empty
# GOVKIT_S2S_TOKEN disables the endpoints entirely (every call 401s).


def _s2s_authorized(request) -> bool:
    """Kept as a name here; the check itself lives in orgs/s2s.py so the other
    S2S surfaces (accounts) share one implementation."""
    from .s2s import authorized

    return authorized(request)


def _invite_payload(invite: Invite, request) -> dict:
    """Everything the doorway needs to render + hand off; accept_url is built HERE."""
    return {
        "code": invite.code,
        "name": invite.name,
        "email": invite.email,
        "link": invite.link,
        "image_url": invite.image_url,
        "venture_name": invite.venture_name,
        "venture_url": invite.venture_url,
        "role": invite.role,
        "audience": invite.audience,
        # Where accepting takes them (org / pool / byov). The doorway words the
        # join page differently for a founder bringing their own venture.
        "kind": invite.kind,
        "drafted_statement": invite.drafted_statement,
        "drafted_social_post": invite.drafted_social_post,
        "status": invite.status,
        "committed_claim_id": invite.committed_claim_id,
        "statement_as_published": invite.statement_as_published,
        "video_url": invite.video_url,
        "expires_at": invite.expires_at.isoformat(),
        # Relayed to the INVITEE's browser by the doorway — must be the public
        # host, never this S2S request's loopback host.
        "accept_url": (
            settings.PUBLIC_BASE_URL + reverse("orgs:accept_invite", kwargs={"code": invite.code})
            if settings.PUBLIC_BASE_URL
            else request.build_absolute_uri(
                reverse("orgs:accept_invite", kwargs={"code": invite.code})
            )
        ),
        "org_slug": invite.org.slug,
        "org_name": invite.org.display_name,
        # The one link to send the person. Doorway invites go to the public page
        # (which shows their card, then the accept step); direct ones go straight
        # to accept. Same rule as the members page's share link.
        "share_url": (
            f"{settings.DOORWAY_BASE_URL}{invite.code}/"
            if invite.doorway and settings.DOORWAY_BASE_URL
            else (
                settings.PUBLIC_BASE_URL
                + reverse("orgs:accept_invite", kwargs={"code": invite.code})
                if settings.PUBLIC_BASE_URL
                else request.build_absolute_uri(
                    reverse("orgs:accept_invite", kwargs={"code": invite.code})
                )
            )
        ),
    }


def _s2s_invite(request, org_slug, code):
    """Shared auth + lookup. Returns (invite, None) or (None, error response)."""
    if not _s2s_authorized(request):
        return None, JsonResponse({"error": "unauthorized"}, status=401)
    invite = Invite.objects.filter(org__slug=org_slug, code=code).select_related("org").first()
    if invite is None:
        return None, JsonResponse({"error": "not_found"}, status=404)
    return invite, None


@require_GET
def invite_detail(request, org_slug, code):
    """Doorway resolves a code to personalize its commit page (status included as-is)."""
    invite, error = _s2s_invite(request, org_slug, code)
    if error:
        return error
    return JsonResponse(_invite_payload(invite, request))


# Bearer-secret auth, not a browser session: skip OrgContextMiddleware's login redirect.
invite_detail.org_context_exempt = True


@csrf_exempt
@require_POST
def invite_committed(request, org_slug, code):
    """
    Doorway posts back the LinkedTrust claim id after the invitee commits.
    Idempotent created→committed; 409 if the invite is revoked or expired;
    already committed/accepted returns 200 with current state, unchanged.
    """
    invite, error = _s2s_invite(request, org_slug, code)
    if error:
        return error
    if invite.status == InviteStatus.REVOKED or invite.is_expired:
        return JsonResponse({"error": "invite_dead", "status": invite.status}, status=409)
    try:
        body = json.loads(request.body or b"{}")
        claim_id = int(body["claim_id"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return JsonResponse({"error": "claim_id (int) is required"}, status=400)
    invite.mark_committed(
        claim_id=claim_id,
        statement_as_published=str(body.get("statement_as_published", "")),
        video_url=str(body.get("video_url", "")),
    )
    return JsonResponse(_invite_payload(invite, request))


invite_committed.org_context_exempt = True


@csrf_exempt
@require_POST
def invite_mint(request, org_slug):
    """S2S: mint an invite for someone who is ALREADY on the wall.

    The other direction from invite_committed. There, a fresh invite is handed
    to a person who then makes a card. Here, the card exists first — a walk-up
    the accelerator decided to bring in — and the invite is bound to it, so
    accepting keeps the card they already have instead of asking for a second
    one. `committed_claim_id` is the only join between a wall claim and an
    account (_accounts_by_claim reads it), so binding it here is what lets
    their profile and bio attach to the card they already made.

    Body: claim_id (int, required), name, email, link, audience, kind, role,
    and `fresh_card` (bool, default false) — false binds the claim and the
    person lands on their own card with an accept button; true leaves the
    claim unbound so they write a new card on the way in.

    `link_code` names a shared InviteLink the person walked through. When it is
    given, the link decides kind/audience/role — the door's terms are the door's
    to state, not the caller's — and the minted invite records which link it came
    from. A shut link mints nothing (409).

    Idempotent on (org, claim_id): a second call returns the invite already
    minted rather than a duplicate. Nothing here approves anything — whether
    the card shows on the wall stays workers.vc's own ledger decision.
    """
    if not _s2s_authorized(request):
        return JsonResponse({"error": "unauthorized"}, status=401)
    org = Org.objects.filter(slug=org_slug).first()
    if org is None:
        return JsonResponse({"error": "not_found"}, status=404)
    try:
        body = json.loads(request.body or b"{}")
        claim_id = int(body["claim_id"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return JsonResponse({"error": "claim_id (int) is required"}, status=400)

    invite_link = None
    link_code = str(body.get("link_code") or "")
    if link_code:
        invite_link = InviteLink.objects.filter(org=org, code=link_code).first()
        if invite_link is None:
            return JsonResponse({"error": "not_found"}, status=404)
        if not invite_link.is_open:
            return JsonResponse(
                {"error": "link_closed", "reason": invite_link.closed_reason}, status=409
            )

    if invite_link is not None:
        kind, audience, role = invite_link.kind, invite_link.audience, invite_link.role
    else:
        kind = str(body.get("kind") or InviteKind.ORG)
        if kind not in InviteKind.values or kind == InviteKind.BYOV:
            # BYOV creates a whole venture org on accept. That is a deliberate
            # founder path, never a conversion of a walk-up.
            return JsonResponse({"error": "kind must be 'org' or 'pool'"}, status=400)
        audience = str(body.get("audience") or InviteAudience.SUPPORTER)
        if audience not in InviteAudience.values:
            return JsonResponse({"error": "unknown audience"}, status=400)
        role = str(body.get("role") or MembershipRole.MEMBER)
        if role not in MembershipRole.values:
            return JsonResponse({"error": "unknown role"}, status=400)

    existing = Invite.objects.filter(org=org, committed_claim_id=claim_id).first()
    if existing is not None:
        return JsonResponse(_invite_payload(existing, request), status=200)

    fresh_card = bool(body.get("fresh_card"))
    invite = Invite.objects.create(
        org=org,
        kind=kind,
        audience=audience,
        role=role,
        from_link=invite_link,
        name=str(body.get("name", ""))[:255],
        email=str(body.get("email", ""))[:254],
        link=str(body.get("link", ""))[:1000],
        # Both paths route through the doorway: keeping the card, it shows them
        # their own card and the accept button; writing a fresh one, it is the
        # ordinary commit page.
        doorway=bool(settings.DOORWAY_BASE_URL),
    )
    if not fresh_card:
        # can_accept allows COMMITTED, so the accept ceremony works straight
        # from here — the person never passes through a second commit step.
        invite.mark_committed(
            claim_id=claim_id,
            statement_as_published=str(body.get("statement_as_published", "")),
            video_url=str(body.get("video_url", "")),
        )
    return JsonResponse(_invite_payload(invite, request), status=201)


invite_mint.org_context_exempt = True


@require_GET
def invite_link_detail(request, org_slug, code):
    """S2S: what a shared link opens, and whether it is still open.

    The doorway renders its own page from this (the audience decides the words
    on it) and then mints a personal invite per person through invite_mint with
    `link_code`. Nothing here creates anything: resolving a link is a read, so a
    crawler or a curious refresh leaves no rows behind. `open` false comes with
    a `reason` the page can say out loud rather than a dead end.
    """
    if not _s2s_authorized(request):
        return JsonResponse({"error": "unauthorized"}, status=401)
    link = InviteLink.objects.filter(org__slug=org_slug, code=code).select_related("org").first()
    if link is None:
        return JsonResponse({"error": "not_found"}, status=404)
    return JsonResponse(
        {
            "code": link.code,
            "kind": link.kind,
            "audience": link.audience,
            "role": link.role,
            "label": link.label,
            "open": link.is_open,
            "reason": link.closed_reason,
            "uses": link.uses,
            "max_uses": link.max_uses,
            "expires_at": link.expires_at.isoformat() if link.expires_at else None,
            "org_slug": link.org.slug,
            "org_name": link.org.display_name,
        }
    )


invite_link_detail.org_context_exempt = True


@require_GET
def org_profile(request, org_slug):
    """S2S: the org profile the cohort top bar needs (calendar/chat/site + repos).

    Same shared-bearer auth as the invite endpoints. workers.vc reads this
    (cached) to stamp the Calendar / Chat links onto the nav, and to say on the
    team's dash whether their join page is ready and where to work on it. No
    members, rates, or governance config — just the public-facing profile the
    team set.

    Both URLs are built HERE. The dash is where a team lands, but GovKit owns
    where its own screens live, so the doorway is handed links rather than
    assembling GovKit paths of its own.
    """
    if not _s2s_authorized(request):
        return JsonResponse({"error": "unauthorized"}, status=401)
    org = Org.objects.filter(slug=org_slug).first()
    if org is None:
        return JsonResponse({"error": "not_found"}, status=404)
    from .views import _join_page_url

    public_base = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    settings_path = reverse("orgs:settings", kwargs={"org_slug": org.slug})
    return JsonResponse(
        {
            "slug": org.slug,
            "display_name": org.display_name,
            "pitch": org.pitch,
            "website": org.website,
            "socials": org.socials or [],
            "repos": org.repos or [],
            "context_repo": org.context_repo,
            "calendar_url": org.calendar_url,
            "chat_url": org.chat_url,
            "pie_url": org.pie_url,
            "pie_as_of": org.pie_as_of.isoformat() if org.pie_as_of else None,
            # The join page: the link the team shares, whether it is worth
            # sharing yet, and where they go to work on it.
            "join_page_url": _join_page_url(org),
            "join_page_ready": org.profile_ready,
            "join_page_edit_url": (
                f"{public_base}{settings_path}#your-page" if public_base else ""
            ),
        }
    )


org_profile.org_context_exempt = True


@require_GET
def ventures_directory(request):
    """S2S: every venture's public card — slug, name, pitch, site, size.

    The workers.vc apex renders the PUBLIC ventures pages server-side from
    this (anonymous visitors, so no session auth can apply). Same bearer as
    the other S2S endpoints. The accelerator is a card like the rest: it is a
    venture itself, raising money and taking people in, and running the cohort
    is a view it ALSO has (golda 2026-07-27). Only what a team chose to publish
    on its join page — never members, rates, or governance config. Member names
    and faces stay out on purpose: a person joined a team, they did not agree to
    be listed on a public page.
    """
    if not _s2s_authorized(request):
        return JsonResponse({"error": "unauthorized"}, status=401)
    qs = (
        Org.objects.annotate(member_count=Count("memberships"))
        .prefetch_related("pictures", "links", "posts", "quotes")
        .order_by("display_name")
    )
    return JsonResponse({"ventures": [_venture_card(o) for o in qs]})


def _venture_card(org):
    """Everything the public venture page renders, and nothing else.

    The three lists a team fills in themselves (pictures, links, posts) travel
    with the card, because the page draws them in one pass and a second call per
    venture would put the directory behind N requests. The feed rule lives on
    the model (Org.public_posts), so what this sends is already what a stranger
    may see: below the bar it is an empty list and the page draws no section.

    The calendar is here ONLY when the team made it public. A private calendar
    must not leave GovKit at all, so it is left out rather than sent with a flag
    for the caller to respect.
    """
    return {
        "slug": org.slug,
        "display_name": org.display_name,
        "tagline": org.tagline,
        "pitch": org.pitch,
        "website": org.website,
        "logo_url": org.logo_url,
        "cover_image_url": org.cover_image_url,
        "asks": org.asks,
        "socials": org.socials or [],
        "member_count": org.member_count,
        "pictures": [
            {"url": p.url, "grid_url": p.grid_url, "caption": p.caption} for p in org.pictures.all()
        ],
        "links": [
            {"title": link.title, "url": link.url, "image_url": link.image_url, "label": link.label}
            for link in org.links.all()
        ],
        "posts": [
            {
                "on": post.happened_on.isoformat(),
                "words": post.words,
                "image_url": post.image_url,
                "grid_url": post.grid_url,
                "link_url": post.link_url,
            }
            for post in org.public_posts()
        ],
        "quotes": [
            {
                "words": q.words,
                "said_by": q.said_by,
                "source_url": q.source_url,
                "said_on": q.said_on.isoformat() if q.said_on else "",
            }
            for q in org.quotes.all()
        ],
        "calendar_url": org.calendar_url if org.calendar_public else "",
    }


ventures_directory.org_context_exempt = True


# --- The public profile, written by an agent ---------------------------------
#
# Everything an admin can do to a team's public page on the settings screen, an
# agent can do here (golda 2026-07-28: "i want everything to be agenticable").
# The screen and these endpoints are two doors onto the same rows; neither is a
# copy of the other's rules, because both go through the same models.
#
# Auth is the existing S2S bearer, the one workers.vc and amebo already hold. It
# is a server secret, not a person's credential, so what it protects is the
# public half of a team's profile: pictures, links, posts, quotes, and the few
# fields that decide how the join page reads. Members, rates, pie and governance
# are NOT reachable from here and must not be added to it.

_PROFILE_FIELDS = {
    "tagline": "tagline",
    "pitch": "pitch",
    "website": "website",
    "logo_url": "logo_url",
    "cover_image_url": "cover_image_url",
    "calendar_url": "calendar_url",
    "calendar_public": "calendar_public",
    # A list, not a string: [{"role": ..., "detail": ...}]. Kept here with the
    # rest of the public profile because it IS the public profile — it is what a
    # stranger decides on — and an agent fixing a line of it should not have to
    # ask a person to retype the lot into a textarea.
    "looking_for": "looking_for",
}


def _clean_asks(value):
    """Return the asks as stored, or None if this is not a list of asks.

    Same shape the settings screen writes (Org.asks reads it back): a role, and
    optionally a line about what that person would do. Anything without a role
    is dropped rather than stored empty, since a nameless ask is not an ask.
    """
    if not isinstance(value, list):
        return None
    asks = []
    for item in value:
        if isinstance(item, str):
            role, _, detail = item.partition(":")
            item = {"role": role, "detail": detail}
        if not isinstance(item, dict):
            return None
        role = str(item.get("role", "")).strip()
        if role:
            asks.append({"role": role[:120], "detail": str(item.get("detail", "")).strip()[:300]})
    return asks


def _body(request):
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


@csrf_exempt
def profile_write(request, org_slug):
    """PATCH the scalar half of a team's public profile, one field or many.

    Only the keys sent are touched, so an agent fixing a tagline cannot blank a
    pitch it never read. Unknown keys are refused rather than ignored: silently
    dropping a field an agent believed it set is how a caller ends up sure it
    saved something it did not.
    """
    if request.method != "PATCH":
        return JsonResponse({"error": "use PATCH"}, status=405)
    if not _s2s_authorized(request):
        return JsonResponse({"error": "unauthorized"}, status=401)
    org = Org.objects.filter(slug=org_slug).first()
    if org is None:
        return JsonResponse({"error": "not_found"}, status=404)
    payload = _body(request)
    if payload is None:
        return JsonResponse({"error": "send a JSON object"}, status=400)
    unknown = sorted(set(payload) - set(_PROFILE_FIELDS))
    if unknown:
        return JsonResponse(
            {"error": "unknown fields", "fields": unknown, "allowed": sorted(_PROFILE_FIELDS)},
            status=400,
        )
    changed = []
    for key, value in payload.items():
        field = _PROFILE_FIELDS[key]
        if field == "calendar_public":
            setattr(org, field, bool(value))
        elif field == "looking_for":
            asks = _clean_asks(value)
            if asks is None:
                return JsonResponse(
                    {"error": "looking_for is a list of {role, detail}"}, status=400
                )
            setattr(org, field, asks)
        else:
            setattr(org, field, str(value).strip())
        changed.append(field)
    if changed:
        org.save(update_fields=[*changed, "updated_at"])
    return JsonResponse({"changed": changed})


profile_write.org_context_exempt = True


def _rows_payload(org):
    """The four lists as they stand, so a caller sees what it just did."""
    return {
        "pictures": [
            {"id": p.id, "url": p.url, "thumb_url": p.thumb_url, "caption": p.caption}
            for p in org.pictures.all()
        ],
        "links": [
            {"id": link.id, "title": link.title, "url": link.url, "image_url": link.image_url}
            for link in org.links.all()
        ],
        "posts": [
            {
                "id": post.id,
                "on": post.happened_on.isoformat(),
                "words": post.words,
                "image_url": post.image_url,
                "link_url": post.link_url,
            }
            for post in org.posts.all()
        ],
        "quotes": [
            {"id": q.id, "words": q.words, "said_by": q.said_by, "source_url": q.source_url}
            for q in org.quotes.all()
        ],
    }


def _next_sort(rows):
    return max((row.sort for row in rows), default=0) + 1


@csrf_exempt
def profile_upload(request, org_slug):
    """POST a file, get back the URL it now lives at.

    An agent holding a picture or a deck has bytes, not a URL, and the bucket
    credentials belong to this app and stay in it. So the file comes here and
    GovKit puts it away: the agent never sees a key. What comes back is only a
    URL, which the caller then puts on a row like any other.
    """
    if request.method != "POST":
        return JsonResponse({"error": "use POST"}, status=405)
    if not _s2s_authorized(request):
        return JsonResponse({"error": "unauthorized"}, status=401)
    org = Org.objects.filter(slug=org_slug).first()
    if org is None:
        return JsonResponse({"error": "not_found"}, status=404)
    upload = request.FILES.get("file")
    if upload is None:
        return JsonResponse({"error": "send a file as 'file'"}, status=400)
    problem = storage.check_file(upload)
    if problem:
        return JsonResponse({"error": problem}, status=400)
    if not storage.configured():
        return JsonResponse({"error": "no bucket configured on this server"}, status=503)
    folder = (request.POST.get("folder") or "org-files").strip("/") or "org-files"
    try:
        url = storage.store_file(upload, prefix=f"{folder}/{org.slug}")
    except Exception:
        logger.exception("s2s upload failed for %s", org.slug)
        return JsonResponse({"error": "the bucket would not take it"}, status=502)
    return JsonResponse({"url": url}, status=201)


profile_upload.org_context_exempt = True


@csrf_exempt
def profile_rows(request, org_slug, kind):
    """GET the four lists, or POST one new row to one of them.

    A picture arrives as a URL here, never as a file. An agent that has the
    picture on disk puts it somewhere first; the browser form is where a file
    goes, because that is where a person has one.
    """
    if not _s2s_authorized(request):
        return JsonResponse({"error": "unauthorized"}, status=401)
    org = Org.objects.filter(slug=org_slug).first()
    if org is None:
        return JsonResponse({"error": "not_found"}, status=404)
    if request.method == "GET":
        return JsonResponse(_rows_payload(org))
    if request.method != "POST":
        return JsonResponse({"error": "use GET or POST"}, status=405)
    payload = _body(request)
    if payload is None:
        return JsonResponse({"error": "send a JSON object"}, status=400)

    def text(key, limit=None):
        value = str(payload.get(key, "") or "").strip()
        return value[:limit] if limit else value

    if kind == "pictures":
        if not text("url"):
            return JsonResponse({"error": "url is required"}, status=400)
        OrgPicture.objects.create(
            org=org,
            url=text("url"),
            thumb_url=text("thumb_url"),
            caption=text("caption", 200),
            sort=_next_sort(org.pictures.all()),
        )
    elif kind == "links":
        if not (text("title") and text("url")):
            return JsonResponse({"error": "title and url are required"}, status=400)
        OrgLink.objects.create(
            org=org,
            title=text("title", 200),
            url=text("url"),
            image_url=text("image_url"),
            sort=_next_sort(org.links.all()),
        )
    elif kind == "posts":
        if not text("words"):
            return JsonResponse({"error": "words is required"}, status=400)
        on = text("on")
        try:
            happened_on = date.fromisoformat(on) if on else date.today()
        except ValueError:
            return JsonResponse({"error": "on must be YYYY-MM-DD"}, status=400)
        OrgPost.objects.create(
            org=org,
            words=text("words"),
            happened_on=happened_on,
            image_url=text("image_url"),
            thumb_url=text("thumb_url"),
            link_url=text("link_url"),
        )
    elif kind == "quotes":
        if not text("words"):
            return JsonResponse({"error": "words is required"}, status=400)
        said_on = text("said_on")
        try:
            when = date.fromisoformat(said_on) if said_on else None
        except ValueError:
            return JsonResponse({"error": "said_on must be YYYY-MM-DD"}, status=400)
        OrgQuote.objects.create(
            org=org,
            words=text("words"),
            said_by=text("said_by", 200),
            source_url=text("source_url"),
            said_on=when,
            sort=_next_sort(org.quotes.all()),
        )
    else:
        return JsonResponse({"error": "no such list"}, status=404)
    return JsonResponse(_rows_payload(org), status=201)


profile_rows.org_context_exempt = True


@csrf_exempt
def profile_row(request, org_slug, kind, row_id):
    """DELETE one row. Adding is the only other thing a list needs; a row that is
    wrong is removed and added again, which is what the screen does too."""
    if request.method != "DELETE":
        return JsonResponse({"error": "use DELETE"}, status=405)
    if not _s2s_authorized(request):
        return JsonResponse({"error": "unauthorized"}, status=401)
    org = Org.objects.filter(slug=org_slug).first()
    if org is None:
        return JsonResponse({"error": "not_found"}, status=404)
    models_by_kind = {
        "pictures": OrgPicture,
        "links": OrgLink,
        "posts": OrgPost,
        "quotes": OrgQuote,
    }
    model = models_by_kind.get(kind)
    if model is None:
        return JsonResponse({"error": "no such list"}, status=404)
    removed, _ = model.objects.filter(org=org, pk=row_id).delete()
    if not removed:
        return JsonResponse({"error": "not_found"}, status=404)
    return JsonResponse(_rows_payload(org))


profile_row.org_context_exempt = True


class OrgDirectoryView(APIView):
    """Every org on this installation, name + slug only, for a signed-in person
    choosing where to go — the workers.vc /welcome cards for someone with no
    membership yet. This is a single-tenant install (no self-hosters; one
    accelerator on the vc VM), so "all orgs" is exactly the accelerator's own
    teams. Unlike OrgViewSet (which scopes to the caller's memberships), this
    lists teams the caller could join; it exposes no config, rates, or members.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        orgs = Org.objects.order_by("display_name").values("slug", "display_name")
        return Response({"orgs": list(orgs)})


class PersonContactView(APIView):
    """How a venture reaches one person on the wall.

    A team that wants to work with somebody has to be able to write to them,
    and the wall deliberately carries no address. So this hands one over —
    only to a person who runs a venture here, and only for somebody who joined
    through an invite, which is the record that holds the address.

    It cannot live on the public page it is used from: that page has no session
    and an address rendered into it would be readable by anybody who looked at
    the source. It is fetched on the press instead, and refused here.
    """

    permission_classes = [AllowAny]

    def get(self, request, claim_id):
        # Two different refusals, and they must not look the same: "sign in"
        # and "you are not a venture" send a person to different places, and a
        # single 403 for both leaves nobody able to say which happened.
        if not request.user.is_authenticated:
            # Returned rather than raised: DRF turns NotAuthenticated into a
            # 403 when the authenticator offers no WWW-Authenticate, which is
            # the very conflation this is here to undo.
            return Response(
                {"detail": "Sign in and we will see who you are."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        runs_a_venture = Membership.objects.filter(
            user=request.user, role=MembershipRole.ADMIN
        ).exists()
        if not (runs_a_venture or request.user.is_superuser):
            raise PermissionDenied("Reaching people is for the teams in the run.")
        invite = (
            Invite.objects.filter(committed_claim_id=claim_id)
            .exclude(status=InviteStatus.REVOKED)
            .order_by("-accepted_at", "-created_at")
            .first()
        )
        if invite is None:
            raise Http404("Nobody here made that card.")
        email = (invite.accepted_by.email if invite.accepted_by_id else "") or invite.email
        if not email:
            raise Http404("We have no address for them.")
        return Response({"name": invite.name, "email": email})


@require_GET
def member_by_discord(request, org_slug, discord_user_id):
    """S2S: who is this Discord user, as far as this org is concerned.

    The team's chat bot needs a name and a role before it will act on anything
    a person types. Identity has one home and this is it — the bot asks each
    time rather than keeping its own copy of who anyone is.

    404 means "nobody here has claimed that Discord id", which is the ordinary
    answer for someone in the server who has not joined the org. Deliberately
    narrow: a name, a role, and the tracker handle. No rates, no equity, no
    email beyond what the person already gave the org.
    """
    if not _s2s_authorized(request):
        return JsonResponse({"error": "unauthorized"}, status=401)
    membership = (
        Membership.objects.filter(org__slug=org_slug, discord_user_id=str(discord_user_id))
        .select_related("user", "org")
        .first()
    )
    if membership is None:
        return JsonResponse({"error": "not_found"}, status=404)
    user = membership.user
    return JsonResponse(
        {
            # get_full_name() is the user's display_name, falling back to email.
            "display_name": user.get_full_name(),
            "email": user.email,
            "role": membership.role,
            "org_slug": membership.org.slug,
            "taiga_username": membership.taiga_username,
            "discord_user_id": membership.discord_user_id,
        }
    )


member_by_discord.org_context_exempt = True


router = DefaultRouter()
router.register(r"orgs", OrgViewSet, basename="org")
router.register(r"memberships", MembershipViewSet, basename="membership")

urlpatterns = router.urls + [
    path("directory/", OrgDirectoryView.as_view(), name="org-directory"),
    path("<slug:org_slug>/checklist/", ChecklistView.as_view(), name="org-checklist"),
    path(
        "<slug:org_slug>/checklist/<str:item_key>/toggle/",
        ChecklistToggleView.as_view(),
        name="org-checklist-toggle",
    ),
    path("ventures/public/", ventures_directory, name="s2s_ventures_directory"),
    path(
        "people/<int:claim_id>/contact/",
        PersonContactView.as_view(),
        name="person-contact",
    ),
    path(
        "<slug:org_slug>/members/by-discord/<str:discord_user_id>/",
        member_by_discord,
        name="s2s_member_by_discord",
    ),
    path("<slug:org_slug>/profile/", org_profile, name="s2s_org_profile"),
    # The public profile, writable by an agent (see profile_write above).
    path("<slug:org_slug>/profile/write/", profile_write, name="s2s_profile_write"),
    path("<slug:org_slug>/profile/upload/", profile_upload, name="s2s_profile_upload"),
    path("<slug:org_slug>/profile/<str:kind>/", profile_rows, name="s2s_profile_rows"),
    path(
        "<slug:org_slug>/profile/<str:kind>/<int:row_id>/",
        profile_row,
        name="s2s_profile_row",
    ),
    # Before the <str:code> route below, or "mint" is read as an invite code.
    path("<slug:org_slug>/invites/mint/", invite_mint, name="s2s_invite_mint"),
    path(
        "<slug:org_slug>/invite-links/<str:code>/",
        invite_link_detail,
        name="s2s_invite_link_detail",
    ),
    path("<slug:org_slug>/invites/<str:code>/", invite_detail, name="s2s_invite_detail"),
    path(
        "<slug:org_slug>/invites/<str:code>/committed/",
        invite_committed,
        name="s2s_invite_committed",
    ),
]
