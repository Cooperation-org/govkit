"""sortition views — placeholder (M2). The sortition agent builds the seeded draw here."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def index(request, org_slug):
    return render(
        request, "sortition/index.html", {"page_title": "Committee", "org_slug": org_slug}
    )
