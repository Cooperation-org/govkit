from django.urls import path

from . import views

app_name = "tasksources"

urlpatterns = [
    path("", views.index, name="index"),
    path("sync/", views.sync_now, name="sync"),
]
