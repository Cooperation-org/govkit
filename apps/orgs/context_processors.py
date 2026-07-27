"""Template context: expose the current org and the tab-nav definition."""

from .models import MembershipRole

# session[LAST_TAB_KEY] = {org_slug: tab url_name} — where this person was last
# working in each org.
LAST_TAB_KEY = "org_last_tab"


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
        # The page people join through. ONE tab, named for what it is, because
        # "Settings" is where you go to change a setting, not where you go to be
        # found. (There were two tabs here pointing at this same URL, both
        # highlighted at once — a person could not tell them apart.)
        tabs.append(
            {
                "label": "Your page",
                "url_name": "orgs:settings",
                "active": view_name == "orgs:settings",
            }
        )
    # Remember which tool this person was last using in this org, so coming back
    # to the org lands where they left off instead of always on the pie. Read on
    # the way in by orgs.views.open_org; only GETs count, so a form post that
    # redirects elsewhere never decides where "the org" means.
    if org is not None and request.method == "GET":
        here = next((t for t in tabs if t["active"]), None)
        if here is not None:
            last = request.session.get(LAST_TAB_KEY) or {}
            if last.get(org.slug) != here["url_name"]:
                last[org.slug] = here["url_name"]
                request.session[LAST_TAB_KEY] = last

    from django.conf import settings

    return {
        "current_org": org,
        "nav_tabs": tabs,
        # The cohort's thin cross-app menu (workers.vc ships it); empty = not mounted.
        "cohort_nav_src": settings.COHORT_NAV_SRC,
        # Stamped as data-vc-org so the menu knows which org is the accelerator
        # (its dash carries a Ventures entry; teams' dashes don't).
        "accelerator_org_slug": settings.ACCELERATOR_ORG_SLUG,
    }
