"""The published page. No login, no org in the path — just the link that was shared."""

from django.urls import path

from . import views

app_name = "comms_public"

urlpatterns = [
    path("<str:token>/", views.public_bulletin, name="bulletin"),
]
