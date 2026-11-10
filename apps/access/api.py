"""
DRF endpoints for tool access. Org-scoped by path, so OrgContextMiddleware resolves
``request.org`` and answers anonymous 401 and non-members 403.

    GET   /api/v1/access/orgs/<slug>/                    tools + people, read live (granters)
    POST  /api/v1/access/orgs/<slug>/grant/              {email, connection, scope} (granters)
    POST  /api/v1/access/orgs/<slug>/revoke/             {email, connection, scope} (granters)
    GET   /api/v1/access/orgs/<slug>/changes/?limit=     the log, newest first (granters)
    GET   /api/v1/access/orgs/<slug>/connections/        the org's tool connections (admin)
    POST  /api/v1/access/orgs/<slug>/connections/        add one (admin)
    PATCH /api/v1/access/orgs/<slug>/connections/<id>/   edit / disable one (admin)

Granters are org admins and members with ``can_grant_access``. Writes need the embed
header (``EmbedSessionAuthentication``). ``admin_secret`` is write-only: it is accepted
on POST / PATCH and never returned, only ``has_secret``.

A grant or revoke answers 200 when the tool did it and 502 when the tool refused or
could not be reached; both bodies are the logged change.
"""

from django.core.exceptions import ValidationError
from django.urls import path
from rest_framework.permissions import SAFE_METHODS
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.orgs.embed_auth import EmbedSessionAuthentication
from apps.orgs.models import MembershipRole

from . import services
from .models import AccessAction, AccessResult, ToolConnection, ToolKind


class _ReadOrEmbedWrite(EmbedSessionAuthentication):
    """Reads need only the session; writes need the embed header (the CSRF stand-in)."""

    def enforce_csrf(self, request):
        if request.method in SAFE_METHODS:
            return
        super().enforce_csrf(request)


def _is_admin(request):
    if request.user.is_superuser:
        return True
    m = request.membership
    return m is not None and m.role == MembershipRole.ADMIN


def change_dict(c):
    return {
        "id": c.pk,
        "connection": c.connection_id,
        "tool": c.connection.display_name,
        "scope": c.scope,
        "email": c.email,
        "action": c.action,
        "result": c.result,
        "detail": c.detail,
        "done_by": c.done_by.email if c.done_by else None,
        "done_at": c.done_at.isoformat(),
    }


def connection_dict(c):
    return {
        "id": c.pk,
        "kind": c.kind,
        "display_name": c.display_name,
        "base_url": c.base_url,
        "database": c.database,
        "admin_login": c.admin_login,
        "has_secret": bool(c.admin_secret),
        "default_role": c.default_role,
        "enabled": c.enabled,
    }


class _AccessView(APIView):
    authentication_classes = [_ReadOrEmbedWrite]

    def forbidden(self):
        return Response({"detail": "Not allowed."}, status=403)


class MatrixView(_AccessView):
    def get(self, request, org_slug):
        if not services.can_grant(request):
            return self.forbidden()
        return Response(services.access_matrix(request.org))


class _ChangeView(_AccessView):
    action = None

    def post(self, request, org_slug):
        if not services.can_grant(request):
            return self.forbidden()
        data = request.data
        try:
            change = services.change_access(
                request.org,
                request.user,
                action=self.action,
                connection_id=data.get("connection"),
                email=data.get("email", ""),
                scope=data.get("scope", ""),
            )
        except services.AccessError as exc:
            return Response({"detail": str(exc)}, status=400)
        status = 200 if change.result == AccessResult.OK else 502
        return Response(change_dict(change), status=status)


class GrantView(_ChangeView):
    action = AccessAction.GRANT


class RevokeView(_ChangeView):
    action = AccessAction.REVOKE


class ChangesView(_AccessView):
    def get(self, request, org_slug):
        if not services.can_grant(request):
            return self.forbidden()
        try:
            limit = max(1, min(int(request.query_params.get("limit", 50)), 500))
        except ValueError:
            limit = 50
        rows = (
            services.AccessChange.objects.for_org(request.org)
            .select_related("connection", "done_by")[:limit]
        )
        return Response({"changes": [change_dict(c) for c in rows]})


FIELDS = ("kind", "display_name", "base_url", "database", "admin_login", "default_role")


def _apply(conn, data):
    for f in FIELDS:
        if f in data:
            setattr(conn, f, (data.get(f) or "").strip())
    if "enabled" in data:
        conn.enabled = data.get("enabled") in (True, "true", "1", 1, "on")
    if data.get("admin_secret"):
        conn.admin_secret = data["admin_secret"]
    if conn.kind not in ToolKind.values:
        raise ValidationError({"kind": "Choose taiga or odoo."})
    conn.full_clean(exclude=["org", "admin_secret"])
    conn.save()
    return conn


class ConnectionsView(_AccessView):
    def get(self, request, org_slug):
        if not _is_admin(request):
            return self.forbidden()
        rows = ToolConnection.objects.for_org(request.org)
        return Response({"connections": [connection_dict(c) for c in rows]})

    def post(self, request, org_slug):
        if not _is_admin(request):
            return self.forbidden()
        try:
            conn = _apply(ToolConnection(org=request.org), request.data)
        except ValidationError as exc:
            return Response({"detail": exc.message_dict}, status=400)
        return Response(connection_dict(conn), status=201)


class ConnectionView(_AccessView):
    def patch(self, request, org_slug, pk):
        if not _is_admin(request):
            return self.forbidden()
        conn = ToolConnection.objects.for_org(request.org).filter(pk=pk).first()
        if conn is None:
            return Response({"detail": "No such tool."}, status=404)
        try:
            conn = _apply(conn, request.data)
        except ValidationError as exc:
            return Response({"detail": exc.message_dict}, status=400)
        return Response(connection_dict(conn))


ORG = "orgs/<slug:org_slug>/"

urlpatterns = [
    path(ORG, MatrixView.as_view(), name="access-matrix"),
    path(ORG + "grant/", GrantView.as_view(), name="access-grant"),
    path(ORG + "revoke/", RevokeView.as_view(), name="access-revoke"),
    path(ORG + "changes/", ChangesView.as_view(), name="access-changes"),
    path(ORG + "connections/", ConnectionsView.as_view(), name="access-connections"),
    path(ORG + "connections/<int:pk>/", ConnectionView.as_view(), name="access-connection"),
]
