from django.apps import AppConfig


class CaptableConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.captable"
    label = "captable"
    verbose_name = "Cap table"
