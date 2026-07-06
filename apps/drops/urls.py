from django.urls import path

from . import views

app_name = "drops"

urlpatterns = [
    path("", views.index, name="index"),
    path("open/", views.open_run, name="open_run"),
    path("<int:run_id>/", views.review, name="review"),
    path("<int:run_id>/approve/", views.approve_run, name="approve_run"),
    path("line/<int:line_id>/adjust/", views.adjust_line, name="adjust_line"),
]
