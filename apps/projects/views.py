"""
Projects views — the org portfolio page and one project's detail page.

Both are read views over apps.projects.services; writes go through the API (amebo,
Marten, scripts) or the admin. request.org / request.membership come from
OrgContextMiddleware (routes live under /o/<org_slug>/).
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from .models import Project
from .services import project_summary


@login_required
def index(request, org_slug):
    projects = (
        Project.objects.for_org(request.org)
        .select_related("lead__user", "deal")
        .prefetch_related("payouts")
    )
    rows = [project_summary(p) | {"name": p.name, "due": p.due, "id": p.id} for p in projects]
    return render(request, "projects/index.html", {"rows": rows, "org_slug": org_slug})


@login_required
def detail(request, org_slug, slug):
    project = get_object_or_404(
        Project.objects.for_org(request.org).select_related("lead__user", "deal"), slug=slug
    )
    return render(
        request,
        "projects/detail.html",
        {"project": project, "summary": project_summary(project), "links": project.links.all()},
    )
