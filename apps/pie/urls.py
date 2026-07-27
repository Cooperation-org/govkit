from django.urls import path

from . import views

app_name = "pie"

urlpatterns = [
    path("", views.index, name="index"),
    path("me/", views.standing, name="standing"),
    # Lock-in: "nothing is final until lock-in by majority decision."
    path("lock/start/", views.lock_start, name="lock_start"),
    path("lock/cast/", views.lock_cast, name="lock_cast"),
    path("lock/close/", views.lock_close, name="lock_close"),
]
