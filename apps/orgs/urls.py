"""
orgs URL config — non-org routes (landing, onboarding) and the org dashboard.

The org-scoped FEATURE includes live in config/urls.py, mounted flatly under
/o/<org_slug>/<feature>/ so each feature app owns a top-level URL namespace
(drops, pie, votes, sortition, exports, tasksources). This keeps `{% url 'drops:index' %}`
simple for feature agents rather than nesting namespaces.
"""

from django.urls import path

from . import views

app_name = "orgs"

urlpatterns = [
    path("", views.landing, name="landing"),
    path("onboarding/", views.onboarding_start, name="onboarding"),
    path("o/<slug:org_slug>/", views.dashboard, name="dashboard"),
]
