"""
Auth views — STUB.

`dev_login` is a temporary, clearly-labelled password login so the app is usable while
OAuth is unwired. It is gated behind settings.GOVKIT_DEV_LOGIN and refuses to run
otherwise. The auth agent replaces this module with the LinkedTrust-OIDC + Google flow.
"""

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth import get_user_model
from django.http import Http404
from django.shortcuts import redirect, render

User = get_user_model()


def dev_login(request):
    """Dev-only email+password login. Disabled unless GOVKIT_DEV_LOGIN is true."""
    if not settings.GOVKIT_DEV_LOGIN:
        raise Http404("Dev login is disabled. Configure OAuth/OIDC login instead.")

    error = None
    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=email, password=password)
        if user is not None:
            login(request, user)
            return redirect(request.GET.get("next") or "orgs:landing")
        error = "Invalid credentials."

    return render(request, "accounts/dev_login.html", {"error": error, "dev_login": True})


def logout_view(request):
    logout(request)
    return redirect("orgs:landing")
