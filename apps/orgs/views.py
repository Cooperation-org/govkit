"""
orgs views: landing / org picker, dashboard, and the onboarding wizard SHELL.

The onboarding logic (create org, set unit/valuation, connect tracker, invite) is stubbed
for the orgs/onboarding agent — routes and templates exist so the flow renders end to end.
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .models import Membership, Org


def landing(request):
    """Public landing + org picker. Shows the current user's orgs if logged in."""
    my_orgs = []
    if request.user.is_authenticated:
        my_orgs = (
            Org.objects.filter(memberships__user=request.user).distinct()
            if not request.user.is_superuser
            else Org.objects.all()
        )
    return render(request, "orgs/landing.html", {"my_orgs": my_orgs})


@login_required
def dashboard(request, org_slug):
    """Org home. request.org / request.membership set by OrgContextMiddleware."""
    return render(
        request,
        "orgs/dashboard.html",
        {"member_count": Membership.objects.filter(org=request.org).count()},
    )


@login_required
def onboarding_start(request):
    """Onboarding wizard SHELL — logic stubbed for the onboarding agent."""
    return render(request, "orgs/onboarding.html", {})
