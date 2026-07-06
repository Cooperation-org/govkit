from django.urls import path

from . import views

app_name = "pie"

urlpatterns = [
    path("", views.index, name="index"),
    path("me/", views.standing, name="standing"),
]
