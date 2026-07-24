"""
Auth views — login page + LinkedTrust OIDC (default) and Google OAuth (secondary) flows.

Both providers use the server-side authorization-code flow and end by logging the resolved
user into a Django SESSION (not a token API). A CSRF-style `state` value is stored in the
session at the start of each flow and verified on callback. A `next` URL (validated against
the host) survives the round trip so a user bounced to login lands where they intended.

`dev_login` remains as an explicitly-gated seam (settings.GOVKIT_DEV_LOGIN) for local dev
and tests only; it is never reachable in production.
"""

from __future__ import annotations

import logging
import secrets

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from apps.orgs.invites import consume_pending_invite

from . import google_oauth, oidc
from .auth_handlers import UserUpsertError, upsert_oauth_user

logger = logging.getLogger(__name__)

STATE_SESSION_KEY = "oauth_state"
NEXT_SESSION_KEY = "post_login_next"


# --- Login page ------------------------------------------------------------------------


def login_page(request):
    """
    The sign-in page: LinkedTrust (primary) + Google (secondary) buttons, plus the
    dev-login form when GOVKIT_DEV_LOGIN is set. If already signed in, go home.
    """
    if request.user.is_authenticated:
        return redirect(_safe_next(request.GET.get("next")) or "orgs:landing")

    next_url = _safe_next(request.GET.get("next"))
    return render(
        request,
        "accounts/login.html",
        {
            "next": next_url or "",
            "dev_login_enabled": settings.GOVKIT_DEV_LOGIN,
            "linkedtrust_configured": bool(settings.LINKEDTRUST_CLIENT_ID),
            "google_configured": bool(settings.GOOGLE_OAUTH_CLIENT_ID),
        },
    )


def logout_view(request):
    logout(request)
    return redirect("orgs:landing")


# --- LinkedTrust OIDC (default) --------------------------------------------------------


def linkedtrust_start(request):
    """Begin the LinkedTrust OIDC flow: stash state + next, redirect to the IdP."""
    return _start_flow(request, oidc.authorize_url, "accounts:linkedtrust_callback")


def linkedtrust_callback(request):
    """IdP redirect target: verify state, exchange the code, upsert + session-login."""
    error = _check_callback(request)
    if error:
        return error
    code = request.GET["code"]
    redirect_uri = request.build_absolute_uri(reverse("accounts:linkedtrust_callback"))
    try:
        tokens = oidc.exchange_code(code, redirect_uri)
        access_token = tokens.get("access_token")
        if not access_token:
            raise oidc.OIDCError("Token response had no access_token")
        userinfo = oidc.get_userinfo(access_token)
        user = upsert_oauth_user("linkedtrust", userinfo)
    except (oidc.OIDCError, UserUpsertError) as exc:
        logger.warning("LinkedTrust login failed: %s", exc)
        return _login_error(request, "LinkedTrust sign-in failed. Please try again.")
    return _complete_login(request, user)


# --- Google OAuth (secondary) ----------------------------------------------------------


def google_start(request):
    """Begin the Google OAuth flow."""
    return _start_flow(request, google_oauth.authorize_url, "accounts:google_callback")


def google_callback(request):
    """Google redirect target: verify state, exchange code, validate id_token, login."""
    error = _check_callback(request)
    if error:
        return error
    code = request.GET["code"]
    redirect_uri = request.build_absolute_uri(reverse("accounts:google_callback"))
    try:
        claims = google_oauth.resolve_identity(code, redirect_uri)
        user = upsert_oauth_user("google", claims)
    except (google_oauth.GoogleOAuthError, UserUpsertError) as exc:
        logger.warning("Google login failed: %s", exc)
        return _login_error(request, "Google sign-in failed. Please try again.")
    return _complete_login(request, user)


# --- Dev-only password login (gated) ---------------------------------------------------


def dev_login(request):
    """Dev/test-only email+password login. 404 unless GOVKIT_DEV_LOGIN is set."""
    if not settings.GOVKIT_DEV_LOGIN:
        raise Http404("Dev login is disabled. Use LinkedTrust or Google sign-in.")

    error = None
    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=email, password=password)
        if user is not None:
            return _complete_login(request, user, next_url=request.POST.get("next"))
        error = "Invalid credentials."

    return render(
        request,
        "accounts/login.html",
        {
            "error": error,
            "next": _safe_next(request.GET.get("next") or request.POST.get("next")) or "",
            "dev_login_enabled": True,
            "linkedtrust_configured": bool(settings.LINKEDTRUST_CLIENT_ID),
            "google_configured": bool(settings.GOOGLE_OAUTH_CLIENT_ID),
        },
    )


# --- Shared flow helpers ---------------------------------------------------------------


def _start_flow(request, url_builder, callback_name):
    state = secrets.token_urlsafe(24)
    request.session[STATE_SESSION_KEY] = state
    request.session[NEXT_SESSION_KEY] = _safe_next(request.GET.get("next")) or ""
    redirect_uri = request.build_absolute_uri(reverse(callback_name))
    try:
        url = url_builder(redirect_uri, state)
    except (oidc.OIDCError, google_oauth.GoogleOAuthError) as exc:
        logger.error("Cannot start OAuth flow: %s", exc)
        return HttpResponseBadRequest("Sign-in is not configured on this server.")
    return redirect(url)


def _check_callback(request):
    """Validate the OAuth callback's error/state/code. Returns an HttpResponse on failure."""
    if request.GET.get("error"):
        return _login_error(request, "Sign-in was cancelled or denied.")
    state = request.GET.get("state")
    expected = request.session.pop(STATE_SESSION_KEY, None)
    if not state or not expected or not secrets.compare_digest(state, expected):
        return _login_error(request, "Sign-in session expired. Please try again.")
    if not request.GET.get("code"):
        return _login_error(request, "Sign-in did not complete. Please try again.")
    return None


def _complete_login(request, user, next_url=None):
    """Log the user in, honor a pending invite, then redirect to next / invite landing / home."""
    login(request, user)
    # Where the pending invite lands them (org join -> cohort front door / org
    # dashboard; pool accept -> pool landing) — the URL logic lives with the invite.
    invite_destination = consume_pending_invite(request)

    destination = _safe_next(next_url) or _safe_next(request.session.pop(NEXT_SESSION_KEY, None))
    if destination:
        return redirect(destination)
    if invite_destination:
        return redirect(invite_destination)
    # A plain login (no next, no pending invite): send a MEMBER straight to their
    # team dashboard via the cohort front door — never the org-list "choose org"
    # page (golda 2026-07-24). One org -> that dash; several -> their first (the
    # dash carries an org switcher). Only a person with zero memberships, or a
    # standalone GovKit with no front door configured, falls to the org-list.
    front_door = _cohort_front_door_for(user)
    if front_door:
        return redirect(front_door)
    return redirect("orgs:landing")


def _login_error(request, message):
    return render(
        request,
        "accounts/login.html",
        {
            "error": message,
            "next": _safe_next(request.session.get(NEXT_SESSION_KEY)) or "",
            "dev_login_enabled": settings.GOVKIT_DEV_LOGIN,
            "linkedtrust_configured": bool(settings.LINKEDTRUST_CLIENT_ID),
            "google_configured": bool(settings.GOOGLE_OAUTH_CLIENT_ID),
        },
        status=400,
    )


def _cohort_front_door_for(user):
    """The dash URL for a member's own org, or None.

    Uses COHORT_FRONT_DOOR (an https template with ``{org_slug}``); unset means a
    standalone GovKit that has no cohort dash, so return None and let the caller
    fall back to the org-list. Picks the member's first org — the dash's own
    switcher covers anyone in several.
    """
    if not settings.COHORT_FRONT_DOOR:
        return None
    from apps.orgs.models import Membership

    membership = (
        Membership.objects.filter(user=user).select_related("org").order_by("id").first()
    )
    if membership is None:
        return None
    return settings.COHORT_FRONT_DOOR.format(org_slug=membership.org.slug)


def _safe_next(url):
    """Guard post-login ?next= against open redirects.

    Relative (same-host) URLs are always allowed, as before. An ABSOLUTE next URL is
    allowed only when it is https and its host is on settings.LOGIN_NEXT_ALLOWED_HOSTS
    (the cohort dash handoff, PLAN-cohort-dash.md item 6) — empty allowlist (default)
    rejects every absolute URL.
    """
    if not url:
        return None
    if url_has_allowed_host_and_scheme(url, allowed_hosts=None, require_https=False):
        return url
    allowed_hosts = set(settings.LOGIN_NEXT_ALLOWED_HOSTS)
    if allowed_hosts and url_has_allowed_host_and_scheme(
        url, allowed_hosts=allowed_hosts, require_https=True
    ):
        return url
    return None
