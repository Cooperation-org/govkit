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

from django.urls import path
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter
from rest_framework.views import APIView

from apps.orgs.models import Membership


class _MembershipSummarySerializer(serializers.ModelSerializer):
    org_slug = serializers.SlugField(source="org.slug", read_only=True)
    org_name = serializers.CharField(source="org.display_name", read_only=True)

    class Meta:
        model = Membership
        fields = ["org_slug", "org_name", "role"]


class MeView(APIView):
    """Return the authenticated user's identity and memberships."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        memberships = (
            Membership.objects.filter(user=user).select_related("org").order_by("org__slug")
        )
        return Response(
            {
                "email": user.email,
                "display_name": user.get_full_name(),
                "avatar_url": user.avatar_url,
                "auth_provider": user.auth_provider,
                "is_superuser": user.is_superuser,
                "memberships": _MembershipSummarySerializer(memberships, many=True).data,
            }
        )


router = DefaultRouter()  # reserved for future account resources

urlpatterns = router.urls + [
    path("me/", MeView.as_view(), name="me"),
]
