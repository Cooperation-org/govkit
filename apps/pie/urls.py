from django.urls import path

from . import views

app_name = "pie"

urlpatterns = [
    path("", views.index, name="index"),
]
