"""Startup checks for settings that fail silently.

Some settings turn a whole feature off when they are blank, and nothing says
so — the page just renders less. The accelerator dash's Activity rail went
empty in production for exactly that reason: ACCELERATOR_ORG_SLUG was never
set on the box, so the rail fell through to "people asking to join this org
as a venture" (none) and dropped the new-joiner and door-queue items it
exists for. Tests set the slug themselves, so they stayed green.

These run under `manage.py check` (and `migrate`, `runserver`). Warnings, not
errors: an install with no accelerator org is a legitimate way to run GovKit.
"""

from django.conf import settings
from django.core.checks import Warning, register


@register()
def accelerator_org_slug_is_set(app_configs, **kwargs):
    if settings.ACCELERATOR_ORG_SLUG:
        return []
    return [
        Warning(
            "ACCELERATOR_ORG_SLUG is empty, so no org's dash gets the Activity rail.",
            hint=(
                "Set it to the accelerator org's slug (workers.vc uses 'vc'). "
                "Leave it empty only if this install has no accelerator, in which "
                "case every dash rail shows just that org's own hand-raises."
            ),
            id="orgs.W001",
        )
    ]


@register()
def doorway_api_is_usable(app_configs, **kwargs):
    """The door queue needs both halves. One without the other reads as 'on'
    but returns nothing, and the failure is only visible as a missing row."""
    base = settings.DOORWAY_API_URL
    token = settings.GOVKIT_S2S_TOKEN
    if bool(base) == bool(token):
        return []
    missing = "GOVKIT_S2S_TOKEN" if base else "DOORWAY_API_URL"
    return [
        Warning(
            f"{missing} is empty, so walk-ups pending at the doorway never reach "
            "the Activity rail.",
            hint="Set both, or neither. The doorway reads the same token from its own env.",
            id="orgs.W002",
        )
    ]
