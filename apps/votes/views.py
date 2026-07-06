"""votes views — placeholder (M2). The votes agent builds create/vote/tally here."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def index(request, org_slug):
    return render(request, "votes/index.html", {"page_title": "Votes", "org_slug": org_slug})
