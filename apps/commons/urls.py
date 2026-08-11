from django.urls import path

from . import views

app_name = "commons"

urlpatterns = [
    path("orgs/", views.orgs_view, name="orgs"),
    path("orgs/<slug:slug>/interest/", views.venture_interest, name="venture_interest"),
    path("ideas/", views.ideas_view, name="ideas"),
    path("ideas/new/", views.idea_create, name="idea_create"),
    path("ideas/<slug:slug>/interest/", views.idea_interest, name="idea_interest"),
    path("pool/", views.pool_view, name="pool"),
    path("pool/<int:claim_id>/cv/", views.pool_cv_view, name="pool_cv"),
]
