"""
DRF API for accounts.

Login itself is a browser redirect flow (LinkedTrust / Google / dev seam), so the useful
JSON surface here is the authenticated session's identity: `GET /api/v1/accounts/me/`
returns the current user and their org memberships. Used by SPA/HTMX callers to render
auth state.

NOTE (orchestrator): mount this router by adding to config/urls.py:
    path("api/v1/accounts/", include("apps.accounts.api")),
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path
from django.views.decorators.http import require_GET
from rest_framework import serializers
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter
from rest_framework.views import APIView

from apps.orgs.models import Invite, InviteKind, InviteStatus, Membership
from apps.orgs.s2s import authorized as s2s_authorized

from .models import ProfileLink


class _MembershipSummarySerializer(serializers.ModelSerializer):
    org_slug = serializers.SlugField(source="org.slug", read_only=True)
    org_name = serializers.CharField(source="org.display_name", read_only=True)
    # The audience on the accepted invite that brought this member in — the same
    # read as apps/orgs/cohorts.py (mentorship has a home on the invite, not a role).
    # Null when no accepted invite exists. Resolved from a per-request map in context
    # to avoid an N+1 across memberships.
    audience = serializers.SerializerMethodField()

    class Meta:
        model = Membership
        fields = ["org_slug", "org_name", "role", "audience"]

    def get_audience(self, membership):
        return self.context.get("audience_by_org_id", {}).get(membership.org_id)


class MeView(APIView):
    """Return the authenticated user's identity and memberships."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        memberships = (
            Membership.objects.filter(user=user).select_related("org").order_by("org__slug")
        )
        # One query for every accepted invite this user holds, mapped by org — so each
        # membership can name the audience that brought them in without a per-row lookup.
        audience_by_org_id = dict(
            Invite.objects.filter(accepted_by=user, status=InviteStatus.ACCEPTED).values_list(
                "org_id", "audience"
            )
        )
        return Response(
            {
                "email": user.email,
                "display_name": user.get_full_name(),
                "avatar_url": user.avatar_url,
                "auth_provider": user.auth_provider,
                "is_superuser": user.is_superuser,
                # Screened into the applicant pool (an accepted pool invite IS the
                # pool state — no membership anywhere). The workers.vc router splits
                # pool from supporter on this.
                "pool": Invite.objects.filter(
                    accepted_by=user, status=InviteStatus.ACCEPTED, kind=InviteKind.POOL
                ).exists(),
                "memberships": _MembershipSummarySerializer(
                    memberships,
                    many=True,
                    context={"audience_by_org_id": audience_by_org_id},
                ).data,
            }
        )


class _PublicProfileLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProfileLink
        fields = ["kind", "label", "handle", "url", "order"]


class PublicProfileView(APIView):
    """Public profile layer for one person, looked up by OIDC subject.

    Consumed S2S by workers.vc to render profile cards. Returns only what the
    person opted into: display name, avatar, bio, and links with is_public=True.
    No auth required because nothing private is served.
    """

    permission_classes = [AllowAny]

    def get(self, request, provider, subject):
        user = get_object_or_404(
            get_user_model(),
            auth_provider=provider,
            auth_provider_id=subject,
            is_active=True,
        )
        links = user.profile_links.filter(is_public=True)
        return Response(
            {
                "display_name": user.get_full_name(),
                "avatar_url": user.avatar_url,
                "bio": user.bio,
                "links": _PublicProfileLinkSerializer(links, many=True).data,
            }
        )


@require_GET
def s2s_identity(request, provider, subject):
    """Who this login is, for another server that holds one.

    `accounts/me` answers the same question but only to the person's own
    browser, so a server holding nothing but an OIDC subject could not ask it.
    That gap is why amebo turned away everyone in the workers pool: no
    membership anywhere reads, from the outside, exactly like nobody.

    Belonging to no org is a real state, not a broken one. A person in the pool
    holds an accepted pool invite and no membership, and this says so plainly:

        {"pool": true, "memberships": [], ...}

    A stranger is 404, which is a different answer and must stay one.

    Plain Django, not DRF: the caller is a server with a shared bearer secret,
    the same channel the doorway and the member directory already use.
    """
    if not s2s_authorized(request):
        return JsonResponse({"error": "unauthorized"}, status=401)

    user = (
        get_user_model()
        .objects.filter(auth_provider=provider, auth_provider_id=subject, is_active=True)
        .first()
    )
    if user is None:
        return JsonResponse({"error": "not found"}, status=404)

    accepted = Invite.objects.filter(accepted_by=user, status=InviteStatus.ACCEPTED)
    audience_by_org_id = dict(accepted.values_list("org_id", "audience"))
    memberships = Membership.objects.filter(user=user).select_related("org").order_by("org__slug")
    return JsonResponse(
        {
            "display_name": user.get_full_name(),
            "email": user.email,
            "pool": accepted.filter(kind=InviteKind.POOL).exists(),
            "memberships": [
                {
                    "org_slug": m.org.slug,
                    "org_name": m.org.display_name,
                    "role": m.role,
                    "audience": audience_by_org_id.get(m.org_id),
                }
                for m in memberships
            ],
        }
    )


router = DefaultRouter()  # reserved for future account resources

urlpatterns = router.urls + [
    path("me/", MeView.as_view(), name="me"),
    path(
        "profiles/<slug:provider>/<path:subject>/",
        PublicProfileView.as_view(),
        name="public_profile",
    ),
    path(
        "s2s/identity/<slug:provider>/<path:subject>/",
        s2s_identity,
        name="s2s_identity",
    ),
]
