"""
Tool access: read who has it (live, from each tool), grant, revoke.

Nothing here stores who has access; the tools hold that. Each grant or revoke appends
one ``AccessChange``. Equity code is never touched: a person here is an email.
"""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from apps.orgs.models import Membership, MembershipRole

from .adapters import ToolError, adapter_for
from .models import AccessAction, AccessChange, AccessResult, ToolConnection

logger = logging.getLogger(__name__)

# GovKit users who signed in through LinkedTrust carry this provider; their OIDC
# subject is what the tools' LinkedTrust SSO matches on.
LINKEDTRUST_PROVIDER = "linkedtrust"


class AccessError(Exception):
    """The request itself is wrong (bad email, unknown tool, unknown scope)."""


def can_grant(request) -> bool:
    """Org admins, members an admin allowed, and superusers."""
    user = request.user
    if user.is_authenticated and user.is_superuser:
        return True
    m = getattr(request, "membership", None)
    return m is not None and (m.role == MembershipRole.ADMIN or m.can_grant_access)


def connections(org):
    return ToolConnection.objects.for_org(org).filter(enabled=True)


def access_matrix(org) -> dict:
    """Every tool's scopes and every person with access, plus every GovKit member.

    A tool that fails carries ``error`` and no scopes; the others still answer.
    """
    tools, found = [], {}
    for conn in connections(org):
        tool = {"id": conn.pk, "label": conn.display_name, "kind": conn.kind, "scopes": []}
        try:
            scopes, people = adapter_for(conn).read()
        except ToolError as exc:
            tool["error"] = str(exc)
            logger.warning("access: reading %s failed: %s", conn, exc)
            tools.append(tool)
            continue
        tool["scopes"] = scopes
        tools.append(tool)
        for email, p in people.items():
            person = found.setdefault(email, {"name": "", "access": {}})
            person["name"] = person["name"] or p["name"]
            person["access"][str(conn.pk)] = sorted(p["scopes"])

    members = (
        Membership.objects.filter(org=org).select_related("user").order_by("user__email")
    )
    rows = []
    seen = set()
    for m in members:
        email = m.user.email.strip().lower()
        if email in seen:
            continue
        seen.add(email)
        p = found.get(email, {})
        rows.append(
            {
                "email": email,
                "name": m.user.display_name or p.get("name", ""),
                "is_member": True,
                "access": p.get("access", {}),
            }
        )
    for email in sorted(set(found) - seen):
        rows.append(
            {
                "email": email,
                "name": found[email]["name"],
                "is_member": False,
                "access": found[email]["access"],
            }
        )
    return {"tools": tools, "people": rows}


def _connection(org, connection_id):
    try:
        return connections(org).get(pk=int(connection_id))
    except (ToolConnection.DoesNotExist, TypeError, ValueError):
        raise AccessError("No such tool.")


def _clean_email(email):
    email = (email or "").strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        raise AccessError("That is not an email address.")
    return email


def _person(email):
    """Name and LinkedTrust subject for an email GovKit knows; blanks otherwise."""
    user = get_user_model().objects.filter(email__iexact=email).first()
    if user is None:
        return "", ""
    uid = user.auth_provider_id if user.auth_provider == LINKEDTRUST_PROVIDER else ""
    return user.display_name, uid


def change_access(org, user, *, action, connection_id, email, scope="") -> AccessChange:
    """Grant or revoke one email's access to one tool scope; returns the logged change."""
    if action not in AccessAction.values:
        raise AccessError("Action is grant or revoke.")
    conn = _connection(org, connection_id)
    email = _clean_email(email)
    scope = (scope or "").strip()
    adapter = adapter_for(conn)
    try:
        if action == AccessAction.GRANT:
            name, oauth_uid = _person(email)
            detail = adapter.grant(email, scope, name=name, oauth_uid=oauth_uid)
        else:
            detail = adapter.revoke(email, scope)
        result = AccessResult.OK
    except ToolError as exc:
        detail, result = str(exc), AccessResult.FAILED
        logger.warning("access: %s %s on %s/%s failed: %s", action, email, conn, scope, exc)
    return AccessChange.objects.create(
        org=org,
        connection=conn,
        scope=scope,
        email=email,
        action=action,
        done_by=user if user.is_authenticated else None,
        result=result,
        detail=detail,
    )
