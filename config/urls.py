"""
Root URL configuration.

Layout:
  /                              landing / org picker              (apps.orgs)
  /onboarding/                   org-creation wizard shell         (apps.orgs)
  /o/<org_slug>/                 org dashboard                     (apps.orgs)
  /o/<org_slug>/<feature>/...    org-scoped feature pages          (feature apps)
  /accounts/...                  auth (dev-login stub + OAuth seams)
  /api/v1/<app>/...              DRF endpoints, one router per app (collision-free)
  /admin/                        Django admin

Org-scoped feature routes are mounted here (flat namespaces: drops, pie, votes,
sortition, exports, tasksources). OrgContextMiddleware sees the `org_slug` kwarg and
populates request.org / request.membership for all of them.

Feature agents: add UI routes inside your own app's urls.py, and DRF viewsets inside
your own app's api.py. Do not edit another app's routing.
"""

from django.contrib import admin
from django.urls import include, path

ORG = "o/<slug:org_slug>/"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    # --- API (one router per app; feature agents register viewsets in <app>/api.py) ---
    path("api/v1/accounts/", include("apps.accounts.api")),
    path("api/v1/orgs/", include("apps.orgs.api")),
    path("api/v1/tasksources/", include("apps.tasksources.api")),
    path("api/v1/drops/", include("apps.drops.api")),
    path("api/v1/pie/", include("apps.pie.api")),
    path("api/v1/exports/", include("apps.exports.api")),
    path("api/v1/votes/", include("apps.votes.api")),
    path("api/v1/sortition/", include("apps.sortition.api")),
    path("api/v1/projects/", include("apps.projects.api")),
    # --- LinkedTrust OIDC seam (uncomment once the package is installed) ---
    # path("api/v1/auth/linkedtrust/", include("linkedtrust_auth.urls")),
    # --- Org-scoped feature pages (flat namespaces) ---
    path(ORG + "drops/", include("apps.drops.urls")),
    path(ORG + "pie/", include("apps.pie.urls")),
    path(ORG + "votes/", include("apps.votes.urls")),
    path(ORG + "committee/", include("apps.sortition.urls")),
    path(ORG + "exports/", include("apps.exports.urls")),
    path(ORG + "tasks/", include("apps.tasksources.urls")),
    path(ORG + "projects/", include("apps.projects.urls")),
    # --- Non-org + dashboard HTML routes ---
    path("", include("apps.orgs.urls")),
]
