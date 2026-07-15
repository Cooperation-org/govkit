"""
orgs URL config — non-org routes (landing, onboarding, invite acceptance) and the
org-scoped dashboard + member/roles admin.

Org-scoped routes keep the `o/<org_slug>/` prefix so OrgContextMiddleware resolves
request.org / request.membership (the middleware keys on the `org_slug` view kwarg). The
org-scoped FEATURE includes (drops, pie, votes, sortition, exports, tasksources) live in
config/urls.py; these org-management routes belong to the `orgs` namespace and stay here.
"""

from django.urls import path

from . import views

app_name = "orgs"

urlpatterns = [
    path("", views.landing, name="landing"),
    path("onboarding/", views.onboarding, name="onboarding"),
    path("invites/<str:code>/accept/", views.accept_invite, name="accept_invite"),
    # Org-scoped (org_slug kwarg → middleware sets request.org / request.membership).
    path("o/<slug:org_slug>/", views.dashboard, name="dashboard"),
    path(
        "o/<slug:org_slug>/checklist/<int:item_id>/toggle/",
        views.checklist_toggle,
        name="checklist_toggle",
    ),
    path("o/<slug:org_slug>/members/", views.members, name="members"),
    path("o/<slug:org_slug>/members/invite/", views.invite_create, name="invite_create"),
    path("o/<slug:org_slug>/members/rate/", views.org_rate, name="org_rate"),
    path(
        "o/<slug:org_slug>/members/<int:membership_id>/update/",
        views.member_update,
        name="member_update",
    ),
]
