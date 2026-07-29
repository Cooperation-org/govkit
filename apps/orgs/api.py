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
  GET  /api/v1/orgs/ventures/public/                      every venture's public card
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
import secrets
from datetime import date

import logging

from django.conf import settings
from django.db.models import Count
from django.http import JsonResponse
from django.urls import path, reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter
from rest_framework.views import APIView

from apps.commons import storage

from .embed_auth import EmbedSessionAuthentication
from .genesis import modules_for, serialize_modules, toggle_item
from .models import (
    Invite,
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
    expected = settings.GOVKIT_S2S_TOKEN
    if not expected:
        return False
    supplied = request.headers.get("Authorization", "")
    return secrets.compare_digest(supplied, f"Bearer {expected}")


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
}


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
    path("<slug:org_slug>/invites/<str:code>/", invite_detail, name="s2s_invite_detail"),
    path(
        "<slug:org_slug>/invites/<str:code>/committed/",
        invite_committed,
        name="s2s_invite_committed",
    ),
]
