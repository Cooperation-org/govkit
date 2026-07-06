"""exports views — placeholder. The exports agent builds import + export pages here."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def index(request, org_slug):
    return render(
        request, "exports/index.html", {"page_title": "Import / Export", "org_slug": org_slug}
    )
