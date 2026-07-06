"""Template context: expose the current org and the tab-nav definition."""


def nav(request):
    """
    Adds `current_org` and `nav_tabs` to every template.

    nav_tabs is a list of (label, url_name) for the org-scoped tabs. base.html resolves
    each with the current org slug so the prefix/base-path applies automatically.
    """
    org = getattr(request, "org", None)
    tabs = [
        ("Drops", "drops:index"),
        ("Pie", "pie:index"),
        ("Votes", "votes:index"),
        ("Committee", "sortition:index"),
    ]
    return {"current_org": org, "nav_tabs": tabs}
