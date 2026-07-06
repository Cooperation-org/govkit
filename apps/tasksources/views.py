"""tasksources views — placeholder. The taiga agent adds the sync UI + missing-value queue."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def index(request, org_slug):
    return render(
        request,
        "tasksources/index.html",
        {"page_title": "Task sources", "org_slug": org_slug},
    )
