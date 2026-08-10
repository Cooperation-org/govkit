"""The published page. No login, no org in the path — just the link that was shared."""

from django.urls import path

from . import views

app_name = "comms_public"

urlpatterns = [
    # Before <str:token>, or "stop" is read as somebody's bulletin token.
    path("stop/<slug:org_slug>/<slug:audience>/", views.unsubscribe, name="unsubscribe"),
    # slug, not str: str is greedy and would swallow the .ics too.
    path("event/<int:edition_id>/<slug:item_id>.ics", views.public_event, name="event"),
    path("<str:token>/", views.public_bulletin, name="bulletin"),
]
