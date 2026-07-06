from django.urls import path

from . import views

app_name = "exports"

urlpatterns = [
    path("", views.index, name="index"),
    path("import/", views.import_upload, name="import"),
    path("export/<slug:format_key>.csv", views.export_csv, name="export"),
]
