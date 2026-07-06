from django.urls import path

from . import views

app_name = "sortition"

urlpatterns = [
    path("", views.index, name="index"),
    path("run/", views.run, name="run"),
    path("<int:draw_id>/", views.detail, name="detail"),
    path("<int:draw_id>/verify/", views.verify, name="verify"),
]
