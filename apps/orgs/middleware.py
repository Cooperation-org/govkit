"""
OrgContextMiddleware — resolves the current org for /o/<org_slug>/ routes.

For any resolved view that carries an `org_slug` kwarg it:
  * looks up the Org (404 if unknown),
  * sets `request.org`,
  * finds the requesting user's Membership and sets `request.membership`,
  * returns 403 if an authenticated non-member (and non-superuser) tries to enter,
  * redirects anonymous users to LOGIN_URL.

Feature-app views under /o/<slug>/ can therefore assume `request.org` and
`request.membership` are populated. Superusers get `request.membership = None` but pass
(so admins/dev can inspect any org).

Views that authenticate by other means (the doorway S2S endpoints use a bearer secret,
not a session) opt out by setting `org_context_exempt = True` on the view function.
"""

from django.contrib.auth.views import redirect_to_login
from django.shortcuts import get_object_or_404, redirect

from .models import Membership, Org


class OrgContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Defaults so every view/template can rely on the attributes existing.
        request.org = None
        request.membership = None
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        slug = view_kwargs.get("org_slug")
        if not slug or getattr(view_func, "org_context_exempt", False):
            return None

        org = get_object_or_404(Org, slug=slug)
        request.org = org

        user = request.user
        if not user.is_authenticated:
            return redirect_to_login(request.get_full_path())

        membership = (
            Membership.objects.select_related("org", "user").filter(org=org, user=user).first()
        )
        if membership is None and not user.is_superuser:
            # Not a member: don't load the org's internal pages, and don't dead-end
            # on a raw 403 — send them to the public "About <org>" stub, where they
            # can see the team and ask to join. about_org is org_context_exempt, so
            # this never loops. (Anonymous users still go to login above — they may
            # be a member who simply isn't signed in yet.)
            return redirect("orgs:about", org_slug=slug)

        request.membership = membership
        return None
