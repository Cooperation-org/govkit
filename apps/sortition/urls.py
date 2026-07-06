from django.urls import path

from . import views

app_name = "sortition"

urlpatterns = [
    path("", views.index, name="index"),
]
