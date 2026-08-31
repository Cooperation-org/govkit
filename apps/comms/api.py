"""Comms publishes its own API, because nothing in GovKit may import it.

One endpoint so far: the workers.vc doorway hands over an address somebody
typed into the join page. The person is on the apex site with no account here,
so there is no session to ride and this is server-to-server on the shared
bearer, the same way sponsor pledges arrive (apps/commons/api.py). The check
itself is borrowed through sources/govkit.py, like every other GovKit fact.

Mounted at /api/v1/comms/ by config/urls.py. Everything here stays inside
apps/comms: on spin-out this file becomes the service's own HTTP surface.
"""

from __future__ import annotations

import json

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import JsonResponse
from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from . import services
from .sources.govkit import caller_is_trusted_server

app_name = "comms_api"


@csrf_exempt
def subscriber_create(request, org_slug):
    """Add one typed address to that org's Supporters list.

    Only the address is required. A name is whatever they chose to give.
    Sending the same address twice is a success, not an error: the person
    pressing a button again wants to be on the list either way.
    """
    if request.method != "POST":
        return JsonResponse({"error": "method_not_allowed"}, status=405)
    if not caller_is_trusted_server(request):
        return JsonResponse({"error": "unauthorized"}, status=401)
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "bad_json"}, status=400)

    email = (data.get("email") or "").strip()
    try:
        validate_email(email)
    except ValidationError:
        return JsonResponse({"error": "bad_email"}, status=400)

    created, address = services.add_typed(org_slug, email, data.get("name") or "")
    return JsonResponse({"email": address, "created": created}, status=201 if created else 200)


# Bearer auth, no session: the org gate must not run (see orgs/middleware.py).
subscriber_create.org_context_exempt = True


urlpatterns = [
    path("<slug:org_slug>/subscribers/", subscriber_create, name="subscriber_create"),
]
