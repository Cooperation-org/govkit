from django.apps import AppConfig


class VotesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.votes"
    label = "votes"
    verbose_name = "Votes"
