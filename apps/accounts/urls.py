"""
Auth routes — STUBBED but wired.

The auth agent implements the real LinkedTrust-OIDC-default + Google-secondary flow.
For now a clearly-labelled dev-only password login keeps the app usable. The URL names
here (login/logout) are the stable seam referenced by settings.LOGIN_URL and templates.
"""

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.dev_login, name="login"),
    path("logout/", views.logout_view, name="logout"),
    # Auth agent: add OAuth entry points here, e.g.
    #   path("linkedtrust/start/", views.linkedtrust_start, name="linkedtrust_start"),
    #   path("google/start/", views.google_start, name="google_start"),
]
