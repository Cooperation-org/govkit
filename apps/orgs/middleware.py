"""
OrgContextMiddleware — resolves the current org for /o/<org_slug>/ routes.

For any resolved view that carries an `org_slug` kwarg it:
  * looks up the Org (404 if unknown),
  * sets `request.org`,
  * finds the requesting user's Membership and sets `request.membership`,
  * sends an authenticated non-member (and non-superuser) to the public About
    stub for a page, and answers 403 for an API call,
  * redirects anonymous users to LOGIN_URL (401 for an API call).

Feature-app views under /o/<slug>/ can therefore assume `request.org` and
`request.membership` are populated. Superusers get `request.membership = None` but pass
(so admins/dev can inspect any org).

Views that authenticate by other means (the doorway S2S endpoints use a bearer secret,
not a session) opt out by setting `org_context_exempt = True` on the view function.
"""

from django.contrib.auth.views import redirect_to_login
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect

from .models import Membership, Org

# Every JSON endpoint is mounted under this prefix (config/urls.py). path_info has
# the BASE_PATH prefix already stripped by FORCE_SCRIPT_NAME, so this holds behind
# a path prefix too.
API_PREFIX = "/api/"


def _is_api(request) -> bool:
    """True for the JSON endpoints, which must never be answered with a page.

    A dash card fetches these with the member's own session and decides whether
    to show itself from the status it gets back. Handing it a 302 to an HTML
    About page instead of a status means it cannot tell "you are not in this
    team" from "here is some HTML", so it renders wrong rather than hiding.
    """
    return request.path_info.startswith(API_PREFIX)


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
            if _is_api(request):
                return JsonResponse({"error": "authentication required"}, status=401)
            return redirect_to_login(request.get_full_path())

        membership = (
            Membership.objects.select_related("org", "user").filter(org=org, user=user).first()
        )
        if membership is None and not user.is_superuser:
            # Not a member: don't load the org's internal pages. A person gets the
            # public "About <org>" stub, where they can see the team and ask to
            # join, rather than dead-ending on a raw 403 (about_org is
            # org_context_exempt, so this never loops). A fetch gets a status it
            # can act on, because a card cannot read a redirect.
            if _is_api(request):
                return JsonResponse({"error": "not a member of this org"}, status=403)
            return redirect("orgs:about", org_slug=slug)

        request.membership = membership
        return None
