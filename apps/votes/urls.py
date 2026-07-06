from django.urls import path

from . import views

app_name = "votes"

urlpatterns = [
    path("", views.index, name="index"),
    path("create/", views.create_vote, name="create"),
    path("<int:vote_id>/", views.detail, name="detail"),
    path("<int:vote_id>/cast/", views.cast, name="cast"),
    path("<int:vote_id>/close/", views.close_vote, name="close"),
    path("<int:vote_id>/results/", views.results, name="results"),
]
