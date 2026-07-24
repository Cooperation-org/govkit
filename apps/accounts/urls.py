"""
Auth routes: the login page, LinkedTrust OIDC (default) + Google OAuth (secondary)
entry/callback pairs, the gated dev-login seam, and logout.

`accounts:login` is the stable name referenced by settings.LOGIN_URL and templates. The
OAuth callback paths here are what must be registered as each provider's redirect_uri:
  LinkedTrust: <base>/accounts/linkedtrust/callback/
  Google:      <base>/accounts/google/callback/
"""

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_page, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("profile/", views.profile, name="profile"),
    # LinkedTrust OIDC (default).
    path("linkedtrust/start/", views.linkedtrust_start, name="linkedtrust_start"),
    path("linkedtrust/callback/", views.linkedtrust_callback, name="linkedtrust_callback"),
    # Google OAuth (secondary).
    path("google/start/", views.google_start, name="google_start"),
    path("google/callback/", views.google_callback, name="google_callback"),
    # Dev/test-only password login (404 unless GOVKIT_DEV_LOGIN is set).
    path("dev-login/", views.dev_login, name="dev_login"),
]
