"""Comms UI routes, org-scoped. Admin-only; the gate is in views."""

from django.urls import path

from . import views

app_name = "comms"

urlpatterns = [
    path("", views.index, name="index"),
    path("<int:pk>/save/", views.save, name="save"),
    path("<int:pk>/back/<str:item_id>/", views.put_back, name="put_back"),
    path("<int:pk>/calendar/add/", views.add_from_calendar, name="add_from_calendar"),
    path("<int:pk>/calendar/refresh/", views.refresh_calendar, name="refresh_calendar"),
    path("<int:pk>/list/import/", views.import_list, name="import_list"),
    path("<int:pk>/ask/", views.ask, name="ask"),
    path("<int:pk>/undo/", views.undo, name="undo"),
    path("<int:pk>/schedule/", views.schedule, name="schedule"),
    path("<int:pk>/send/", views.send_now, name="send_now"),
    path("<int:pk>/reopen/", views.reopen, name="reopen"),
    path("<int:pk>/publish/", views.publish, name="publish"),
    path("<int:pk>/unpublish/", views.unpublish, name="unpublish"),
]
