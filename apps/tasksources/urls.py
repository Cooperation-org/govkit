from django.urls import path

from . import views

app_name = "tasksources"

urlpatterns = [
    path("", views.index, name="index"),
    path("sources/save/", views.save_source, name="save_source"),
    path("sync/", views.sync_now, name="sync"),
]
