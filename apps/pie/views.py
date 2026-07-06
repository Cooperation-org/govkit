"""pie views — placeholder. The pie agent builds org pie + personal standing pages."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def index(request, org_slug):
    return render(request, "pie/index.html", {"page_title": "Pie", "org_slug": org_slug})
