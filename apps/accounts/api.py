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
from django.shortcuts import get_object_or_404
from django.urls import path
from rest_framework import serializers
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter
from rest_framework.views import APIView

from apps.orgs.models import Invite, InviteStatus, Membership

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


router = DefaultRouter()  # reserved for future account resources

urlpatterns = router.urls + [
    path("me/", MeView.as_view(), name="me"),
    path(
        "profiles/<slug:provider>/<path:subject>/",
        PublicProfileView.as_view(),
        name="public_profile",
    ),
]
