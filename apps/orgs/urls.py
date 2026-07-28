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
    # Accelerator-admin cross-org oversight (not superuser-only).
    path("teams/", views.all_teams, name="all_teams"),
    # The cohort's mentors + the calendars they shared. Team admins only.
    path("mentors/", views.mentors, name="mentors"),
    # Cohort-wide: program staff and mentors see every team's curriculum progress.
    path("cohorts/<slug:cohort_slug>/", views.cohort_progress_view, name="cohort_progress"),
    # Public "About <org>" stub — where non-members land (org_context_exempt).
    path("o/<slug:org_slug>/about/", views.about_org, name="about"),
    # Org-scoped (org_slug kwarg → middleware sets request.org / request.membership).
    path("o/<slug:org_slug>/", views.dashboard, name="dashboard"),
    # Where outside menus point when they mean "the org": the tool this person
    # was last using there.
    path("o/<slug:org_slug>/open/", views.open_org, name="open_org"),
    path(
        "o/<slug:org_slug>/checklist/<str:item_key>/toggle/",
        views.checklist_toggle,
        name="checklist_toggle",
    ),
    path(
        "o/<slug:org_slug>/checklist/seed/",
        views.checklist_seed,
        name="checklist_seed",
    ),
    path("o/<slug:org_slug>/settings/", views.org_settings, name="settings"),
    # Draft the team profile from the team's own website (posts back to settings).
    path("o/<slug:org_slug>/settings/pull/", views.profile_pull, name="profile_pull"),
    # The three lists on the join page. Each row is added and removed on its
    # own, so adding a picture never re-saves the rest of the profile.
    path("o/<slug:org_slug>/settings/pictures/add/", views.picture_add, name="picture_add"),
    path(
        "o/<slug:org_slug>/settings/pictures/<int:picture_id>/remove/",
        views.picture_remove,
        name="picture_remove",
    ),
    path("o/<slug:org_slug>/settings/links/add/", views.link_add, name="link_add"),
    path(
        "o/<slug:org_slug>/settings/links/<int:link_id>/remove/",
        views.link_remove,
        name="link_remove",
    ),
    path("o/<slug:org_slug>/settings/quotes/add/", views.quote_add, name="quote_add"),
    path(
        "o/<slug:org_slug>/settings/quotes/<int:quote_id>/remove/",
        views.quote_remove,
        name="quote_remove",
    ),
    path("o/<slug:org_slug>/settings/posts/add/", views.post_add, name="post_add"),
    path(
        "o/<slug:org_slug>/settings/posts/<int:post_id>/remove/",
        views.post_remove,
        name="post_remove",
    ),
    path("o/<slug:org_slug>/members/", views.members, name="members"),
    path("o/<slug:org_slug>/members/invite/", views.invite_create, name="invite_create"),
    path(
        "o/<slug:org_slug>/members/invites/<int:invite_id>/revoke/",
        views.invite_revoke,
        name="invite_revoke",
    ),
    path(
        "o/<slug:org_slug>/members/invites/<int:invite_id>/delete/",
        views.invite_delete,
        name="invite_delete",
    ),
    path("o/<slug:org_slug>/members/rate/", views.org_rate, name="org_rate"),
    path(
        "o/<slug:org_slug>/members/<int:membership_id>/remove/",
        views.member_remove,
        name="member_remove",
    ),
    path(
        "o/<slug:org_slug>/members/<int:membership_id>/update/",
        views.member_update,
        name="member_update",
    ),
    path(
        "o/<slug:org_slug>/members/<int:membership_id>/grant/",
        views.member_grant_value,
        name="member_grant_value",
    ),
    path(
        "o/<slug:org_slug>/sponsors/grant/",
        views.sponsor_grant,
        name="sponsor_grant",
    ),
    path(
        "o/<slug:org_slug>/sponsors/<int:stake_id>/remove/",
        views.sponsor_stake_remove,
        name="sponsor_stake_remove",
    ),
    path(
        "o/<slug:org_slug>/pie/launch/",
        views.pie_launch,
        name="pie_launch",
    ),
]
