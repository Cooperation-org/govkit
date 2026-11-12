"""
The welcome a newly joined member sees on whatever page they land on.

What they joined, and the team's own tools with whether each is ready for them
yet. Readiness is read live from the tool itself (apps.access.services.has_access:
is this person's email a user of that team's CRM, a member of its boards?) — the
tools are the record of who has access (docs/BOUNDARIES.md). When GovKit has no
connection to a tool, there is nothing here that knows, and the answer says so
("unknown") instead of guessing.

Rendered by <govkit-welcome> (static/embed/govkit.js), which the cohort dash and
GovKit's own dashboard mount.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.access.models import ToolKind

# A member counts as newly joined for this long, unless they close the panel first.
WELCOME_DAYS = 7

READY = "ready"
SETTING_UP = "setting_up"
UNKNOWN = "unknown"

# The team tools the panel links to: (key, label, URL-template setting, access ToolKind).
# The ToolKind is the tool apps.access reads to answer whether it is ready.
_TOOLS = (
    ("crm", "CRM", "COHORT_CRM_URL", ToolKind.ODOO),
    ("board", "Board", "COHORT_BOARD_URL", ToolKind.TAIGA),
)


def is_new(membership) -> bool:
    if membership.welcome_dismissed_at is not None:
        return False
    return membership.created_at >= timezone.now() - timedelta(days=WELCOME_DAYS)


def _status(access: dict, kind: str) -> str:
    found = access.get(kind)
    if found is None:
        return UNKNOWN
    return READY if found else SETTING_UP


def welcome(membership) -> dict:
    """The panel for `membership`; {"show": False} once it no longer applies."""
    if not is_new(membership):
        return {"show": False}
    from apps.access.services import has_access

    org = membership.org
    tools = [
        (key, label, getattr(settings, setting).format(org_slug=org.slug), kind)
        for key, label, setting, kind in _TOOLS
        if getattr(settings, setting)
    ]
    access = has_access(org, membership.user.email) if tools else {}
    return {
        "show": True,
        "org_slug": org.slug,
        "org_name": org.display_name,
        "role": membership.get_role_display(),
        "joined_at": membership.created_at.isoformat(),
        "tools": [
            {"key": key, "label": label, "url": url, "status": _status(access, kind)}
            for key, label, url, kind in tools
        ],
    }


def dismiss(membership) -> None:
    membership.welcome_dismissed_at = timezone.now()
    membership.save(update_fields=["welcome_dismissed_at"])
