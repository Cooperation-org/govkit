"""
Cohort-wide views of the curriculum: how every team in one run is doing.

Who may look, and why it is not a role. Program staff are admins or stewards of
the accelerator org, which is an ordinary Membership. Mentors are NOT: a mentor's
commitment is that they keep some time open, not that they help govern anything
(golda 2026-07-22), so making them stewards to let them see progress would buy a
read-only view with a governance right. Mentorship already has a home — the
audience on the invite that brought them in — so it is read from there.

The per-team numbers come from apps.orgs.genesis, unchanged: this module adds no
second way to compute progress.
"""

from __future__ import annotations

from .genesis import modules_for, serialize_modules
from .models import InviteAudience, InviteStatus, Membership, MembershipRole, Org


def is_program_staff(user, accelerator_org) -> bool:
    """Admin or steward of the org that runs the cohort."""
    return Membership.objects.filter(
        org=accelerator_org,
        user=user,
        role__in=[MembershipRole.ADMIN, MembershipRole.STEWARD],
    ).exists()


def is_mentor(user, accelerator_org) -> bool:
    """Accepted a mentor invite to the accelerator. Not a role, not governance."""
    return accelerator_org.invites.filter(
        audience=InviteAudience.MENTOR,
        status=InviteStatus.ACCEPTED,
        accepted_by=user,
    ).exists()


def can_view_cohort(user, cohort) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return is_program_staff(user, cohort.accelerator_org) or is_mentor(user, cohort.accelerator_org)


def teams_in(cohort):
    """Teams of one cohort that are on the path, in a stable display order."""
    return Org.objects.filter(cohort=cohort, genesis_started_at__isnull=False).order_by(
        "display_name"
    )


def cohort_progress(cohort):
    """
    Every team's curriculum standing, for the staff/mentor overview.

    One row per team: the same modules the team sees on its own dash, plus the
    totals. No ranking and no flags. The point is to see where the guide is
    working, not to score anyone.
    """
    rows = []
    for org in teams_in(cohort):
        modules = modules_for(org)
        rows.append(
            {
                "org": org,
                "modules": modules,
                "done": sum(m["done"] for m in modules),
                "total": sum(m["total"] for m in modules),
            }
        )
    return rows


def serialize_progress(rows):
    return [
        {
            "org_slug": row["org"].slug,
            "display_name": row["org"].display_name,
            "done": row["done"],
            "total": row["total"],
            "modules": serialize_modules(row["modules"]),
        }
        for row in rows
    ]


def item_skip_counts(cohort):
    """
    Per item, how many teams in the cohort have it done — the one number that
    says whether the guide is any good. An item every team skips is a question
    about the curriculum, not about the teams.
    """
    rows = cohort_progress(cohort)
    counts = {}
    for row in rows:
        for module in row["modules"]:
            for item in module["items"]:
                entry = counts.setdefault(
                    item.key,
                    {"key": item.key, "title": item.title, "module": module["key"], "done": 0},
                )
                if item.done:
                    entry["done"] += 1
    return {"teams": len(rows), "ranking": sorted(counts.values(), key=lambda c: c["done"])}
