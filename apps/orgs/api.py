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
  POST /api/v1/orgs/orgs/{slug}/invite/         admin: generate an invite link
  POST /api/v1/orgs/orgs/{slug}/rate/           admin: set org-wide default rate
  PATCH /api/v1/orgs/memberships/{id}/          admin: set a member's role / rate override
"""

from __future__ import annotations

from django.urls import reverse
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from .invites import make_invite_token
from .models import Membership, MembershipRole, Org
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
        token = make_invite_token(
            org=org,
            role=serializer.validated_data["role"],
            email=serializer.validated_data["email"],
            hourly_rate=serializer.validated_data.get("hourly_rate"),
        )
        link = request.build_absolute_uri(reverse("orgs:accept_invite") + f"?token={token}")
        return Response({"invite_link": link, "token": token}, status=status.HTTP_201_CREATED)

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


router = DefaultRouter()
router.register(r"orgs", OrgViewSet, basename="org")
router.register(r"memberships", MembershipViewSet, basename="membership")

urlpatterns = router.urls
