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

Doorway S2S (plain Django views, Bearer settings.GOVKIT_S2S_TOKEN — NOT session auth;
the magic-link contract on the coordination board):
  GET  /api/v1/orgs/{org_slug}/invites/{code}/            invite detail for the doorway
  POST /api/v1/orgs/{org_slug}/invites/{code}/committed/  doorway posts the claim id back
"""

from __future__ import annotations

import json
import secrets

from django.conf import settings
from django.http import JsonResponse
from django.urls import path, reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from .models import Invite, InviteStatus, Membership, MembershipRole, Org
from .serializers import (
    InviteSerializer,
    MembershipSerializer,
    OnboardingSerializer,
    OrgRateSerializer,
    OrgSerializer,
)


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
        "accept_url": request.build_absolute_uri(
            reverse("orgs:accept_invite", kwargs={"code": invite.code})
        ),
        "org_slug": invite.org.slug,
        "org_name": invite.org.display_name,
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


router = DefaultRouter()
router.register(r"orgs", OrgViewSet, basename="org")
router.register(r"memberships", MembershipViewSet, basename="membership")

urlpatterns = router.urls + [
    path("<slug:org_slug>/invites/<str:code>/", invite_detail, name="s2s_invite_detail"),
    path(
        "<slug:org_slug>/invites/<str:code>/committed/",
        invite_committed,
        name="s2s_invite_committed",
    ),
]
