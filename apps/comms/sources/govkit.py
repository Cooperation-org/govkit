"""
The one file in comms that touches GovKit.

Everything comms needs to know from GovKit is a function here, and the list of
functions is the exact API a spun-out comms service would have to be given
(see ../BOUNDARIES.md). Nothing else in comms imports `apps.*`; the test in
`tests/test_comms.py` fails the build if it does.

Facts named here: an org's display name, its calendar URL, the run it is part of
and when that run started, how many people are in each audience, and whether the
person looking is an admin.
"""

from __future__ import annotations

import logging

from apps.orgs.models import MembershipRole, Org

logger = logging.getLogger(__name__)


def org(org_slug: str):
    """The org row, or None."""
    return Org.objects.filter(slug=org_slug).first()


def display_name(org_slug: str) -> str:
    row = org(org_slug)
    return row.display_name if row else org_slug


def calendar_url(org_slug: str) -> str:
    """The team's own calendar share link, set on the org settings page."""
    row = org(org_slug)
    return (row.calendar_url or "").strip() if row else ""


def calendar_settings_url(org_slug: str) -> str:
    """Where an admin goes to put a calendar in, when there isn't one."""
    from django.urls import reverse

    return reverse("orgs:settings", kwargs={"org_slug": org_slug})


def cohort_start(org_slug: str):
    """The date the run this org belongs to began, or None.

    Read as the accelerator first (it runs the cohort) and then as a team in one.
    Only used to say which week of the run an edition is; a missing date means
    the week is unnumbered, never a guess.
    """
    row = org(org_slug)
    if row is None:
        return None
    run = row.cohorts_run.order_by("-starts_on").first() or row.cohort
    return run.starts_on if run else None


def audience_size(org_slug: str, audience: str):
    """How many people are in one audience, or None when it is not knowable here.

    None renders as nothing. A made-up recipient count on a screen whose next
    button sends email is worse than no number at all.
    """
    row = org(org_slug)
    if row is None:
        return None
    if audience == "v":
        run = row.cohorts_run.order_by("-starts_on").first()
        return run.teams.count() if run else None
    if audience == "m":
        from apps.orgs import doorway

        mentors, problem = doorway.mentors()
        return None if problem else len(mentors)
    return None


def viewer_is_admin(request) -> bool:
    """Comms is admin-only. Superusers pass so the team can inspect any org."""
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and user.is_superuser:
        return True
    membership = getattr(request, "membership", None)
    return membership is not None and membership.role == MembershipRole.ADMIN
