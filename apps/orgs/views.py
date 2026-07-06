"""
orgs views: landing / org picker, dashboard, the onboarding wizard, and the member/roles
admin (invites + role/rate management).

Org-scoped views (dashboard, members, invites, roles) live under /o/<org_slug>/ so
OrgContextMiddleware sets request.org / request.membership. Admin-only actions are gated
with `_require_admin`. Every UI action here has a matching DRF endpoint in api.py.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .forms import InviteForm, MemberUpdateForm, OnboardingForm, OrgRateForm
from .invites import (
    SESSION_KEY,
    InviteError,
    accept_invite_for_user,
    make_invite_token,
    read_invite_token,
)
from .models import Membership, MembershipRole, Org


def landing(request):
    """Public landing + org picker. Shows the current user's orgs if logged in."""
    my_orgs = []
    if request.user.is_authenticated:
        my_orgs = (
            Org.objects.all()
            if request.user.is_superuser
            else Org.objects.filter(memberships__user=request.user).distinct()
        )
    return render(request, "orgs/landing.html", {"my_orgs": my_orgs})


@login_required
def dashboard(request, org_slug):
    """Org home. request.org / request.membership set by OrgContextMiddleware."""
    return render(
        request,
        "orgs/dashboard.html",
        {"member_count": Membership.objects.filter(org=request.org).count()},
    )


@login_required
def onboarding(request):
    """One-flow org setup: create Org + ValuationConfig + admin Membership, land on dash."""
    if request.method == "POST":
        form = OnboardingForm(request.POST)
        if form.is_valid():
            org = form.save(request.user)
            messages.success(request, f"{org.display_name} is ready.")
            return redirect("orgs:dashboard", org_slug=org.slug)
    else:
        form = OnboardingForm()
    return render(request, "orgs/onboarding.html", {"form": form})


# --- Member / roles admin --------------------------------------------------------------


def _require_admin(request):
    """Allow org admins (and superusers). Raise PermissionDenied otherwise."""
    if request.user.is_authenticated and request.user.is_superuser:
        return
    membership = request.membership
    if membership is None or membership.role != MembershipRole.ADMIN:
        raise PermissionDenied("Only organization admins may manage members.")


@login_required
def members(request, org_slug):
    """Admin UI: list members with role + rate controls, org-wide rate, and an invite form."""
    _require_admin(request)
    memberships = (
        Membership.objects.filter(org=request.org)
        .select_related("user", "org")
        .order_by("user__email")
    )
    return render(
        request,
        "orgs/members.html",
        {
            "memberships": memberships,
            "roles": MembershipRole.choices,
            "invite_form": InviteForm(),
            "rate_form": OrgRateForm(
                initial={"default_hourly_rate": request.org.default_hourly_rate}
            ),
        },
    )


@login_required
@require_POST
def invite_create(request, org_slug):
    """Admin generates a shareable invite link for an org (by email, with a role/rate)."""
    _require_admin(request)
    form = InviteForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please correct the invite details.")
        return redirect("orgs:members", org_slug=request.org.slug)

    token = make_invite_token(
        org=request.org,
        role=form.cleaned_data["role"],
        email=form.cleaned_data["email"],
        hourly_rate=form.cleaned_data.get("hourly_rate"),
    )
    link = request.build_absolute_uri(reverse("orgs:accept_invite") + f"?token={token}")
    messages.success(
        request,
        f"Invite link for {form.cleaned_data['email']} (share it directly): {link}",
    )
    return redirect("orgs:members", org_slug=request.org.slug)


@login_required
@require_POST
def member_update(request, org_slug, membership_id):
    """Admin sets a member's role and per-member hourly-rate override."""
    _require_admin(request)
    membership = Membership.objects.filter(org=request.org, id=membership_id).first()
    if membership is None:
        messages.error(request, "That member was not found.")
        return redirect("orgs:members", org_slug=request.org.slug)

    form = MemberUpdateForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please correct the member details.")
        return redirect("orgs:members", org_slug=request.org.slug)

    new_role = form.cleaned_data["role"]
    if (
        membership.role == MembershipRole.ADMIN
        and new_role != MembershipRole.ADMIN
        and not _has_other_admin(request.org, membership)
    ):
        messages.error(request, "An organization must keep at least one admin.")
        return redirect("orgs:members", org_slug=request.org.slug)

    membership.role = new_role
    membership.hourly_rate = form.cleaned_data.get("hourly_rate")
    membership.save(update_fields=["role", "hourly_rate"])
    messages.success(request, "Member updated.")
    return redirect("orgs:members", org_slug=request.org.slug)


@login_required
@require_POST
def org_rate(request, org_slug):
    """Admin sets the org-wide default hourly rate."""
    _require_admin(request)
    form = OrgRateForm(request.POST)
    if form.is_valid():
        request.org.default_hourly_rate = form.cleaned_data.get("default_hourly_rate")
        request.org.save(update_fields=["default_hourly_rate"])
        messages.success(request, "Org-wide default rate updated.")
    else:
        messages.error(request, "Please enter a valid rate.")
    return redirect("orgs:members", org_slug=request.org.slug)


def _has_other_admin(org, excluding):
    return (
        Membership.objects.filter(org=org, role=MembershipRole.ADMIN)
        .exclude(id=excluding.id)
        .exists()
    )


# --- Invite acceptance -----------------------------------------------------------------


def accept_invite(request):
    """
    Land here from an invite link. If signed in, join immediately; otherwise stash the
    token and route through login (consume_pending_invite finishes the join).
    """
    token = request.GET.get("token", "")
    try:
        read_invite_token(token)  # validate before doing anything
    except InviteError as exc:
        messages.error(request, str(exc))
        return redirect("orgs:landing")

    if request.user.is_authenticated:
        try:
            membership = accept_invite_for_user(token, request.user)
        except InviteError as exc:
            messages.error(request, str(exc))
            return redirect("orgs:landing")
        messages.success(request, f"You have joined {membership.org.display_name}.")
        return redirect("orgs:dashboard", org_slug=membership.org.slug)

    request.session[SESSION_KEY] = token
    return redirect("accounts:login")
