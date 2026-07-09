"""Template context: expose the current org and the tab-nav definition."""


def nav(request):
    """
    Adds `current_org` and `nav_tabs` to every template.

    nav_tabs is a list of (label, url_name, namespace) for the org-scoped tabs.
    base.html resolves each with the current org slug so the prefix/base-path applies
    automatically, and compares namespace to the resolved request to mark the
    current tab (aria-current).
    """
    org = getattr(request, "org", None)
    tabs = [
        ("Drops", "drops:index", "drops"),
        ("Pie", "pie:index", "pie"),
        ("Votes", "votes:index", "votes"),
        ("Committee", "sortition:index", "sortition"),
    ]
    return {"current_org": org, "nav_tabs": tabs}
