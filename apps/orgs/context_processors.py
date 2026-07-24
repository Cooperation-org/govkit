"""Template context: expose the current org and the tab-nav definition."""

from .models import MembershipRole


def nav(request):
    """
    Adds `current_org` and `nav_tabs` to every template.

    nav_tabs is a list of {label, url_name, active} dicts for the org-scoped tabs.
    base.html resolves each with the current org slug so the prefix/base-path applies
    automatically; `active` marks the current tab (aria-current). Members — the people
    the whole toolkit is about — is a first-class tab for org admins.
    """
    org = getattr(request, "org", None)
    rm = getattr(request, "resolver_match", None)
    namespace = getattr(rm, "namespace", "")
    view_name = getattr(rm, "view_name", "")

    tabs = [
        {"label": label, "url_name": url_name, "active": namespace == ns}
        for label, url_name, ns in (
            ("Drops", "drops:index", "drops"),
            ("Pie", "pie:index", "pie"),
            ("Votes", "votes:index", "votes"),
            ("Committee", "sortition:index", "sortition"),
        )
    ]
    # Projects is an optional module: the tab appears only once the org uses it.
    if org is not None:
        from apps.projects.models import Project

        if Project.objects.for_org(org).exists():
            tabs.append(
                {
                    "label": "Projects",
                    "url_name": "projects:index",
                    "active": namespace == "projects",
                }
            )
    membership = getattr(request, "membership", None)
    if membership is not None and membership.role == MembershipRole.ADMIN:
        tabs.append(
            {
                "label": "Members",
                "url_name": "orgs:members",
                "active": view_name == "orgs:members",
            }
        )
        tabs.append(
            {
                "label": "Settings",
                "url_name": "orgs:settings",
                "active": view_name == "orgs:settings",
            }
        )
    from django.conf import settings

    return {
        "current_org": org,
        "nav_tabs": tabs,
        # The cohort's thin cross-app menu (workers.vc ships it); empty = not mounted.
        "cohort_nav_src": settings.COHORT_NAV_SRC,
    }
