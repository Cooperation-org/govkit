"""drops views — placeholder. The drops agent builds the open/review/approve flow here."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def index(request, org_slug):
    return render(request, "drops/index.html", {"page_title": "Drops", "org_slug": org_slug})
