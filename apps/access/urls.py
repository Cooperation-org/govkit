from django.urls import path

from . import views

app_name = "access"

urlpatterns = [
    path("", views.index, name="index"),
    path("tools/", views.save_tool, name="save_tool"),
]
