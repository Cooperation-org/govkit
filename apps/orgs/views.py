"""
orgs views: landing / org picker, dashboard, the onboarding wizard, and the member/roles
admin (invites + role/rate management).

Org-scoped views (dashboard, members, invites, roles) live under /o/<org_slug>/ so
OrgContextMiddleware sets request.org / request.membership. Admin-only actions are gated
with `_require_admin`. Every UI action here has a matching DRF endpoint in api.py.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import InviteForm, MemberUpdateForm, OnboardingForm, OrgRateForm
from .genesis import modules_for
from .invites import (
    SESSION_KEY,
    InviteError,
    accept_invite_for_user,
    cohort_front_door_url,
    get_invite_for_accept,
)
from .models import ChecklistItem, Invite, InviteStatus, Membership, MembershipRole, Org


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
    """
    Org home. Venture orgs get the module checklist (side index, any order); orgs
    without one get the plain home. request.org / request.membership set by
    OrgContextMiddleware.
    """
    return render(
        request,
        "orgs/dashboard.html",
        {
            "member_count": Membership.objects.filter(org=request.org).count(),
            "modules": modules_for(request.org),
        },
    )


@login_required
@require_POST
def checklist_toggle(request, org_slug, item_id):
    """Check/uncheck a genesis item. Any member; records who and when."""
    if request.membership is None and not request.user.is_superuser:
        raise PermissionDenied("Only members may work the checklist.")
    item = ChecklistItem.objects.filter(org=request.org, id=item_id).first()
    if item is None:
        raise Http404("No such checklist item.")
    if item.done_at:
        item.done_at = None
        item.done_by = None
    else:
        item.done_at = timezone.now()
        item.done_by = request.user
    item.save(update_fields=["done_at", "done_by"])
    return redirect(
        f"{reverse('orgs:dashboard', kwargs={'org_slug': request.org.slug})}#module-{item.module}"
    )


@login_required
@require_POST
def checklist_seed(request, org_slug):
    """Admin starts the path for an org that has no checklist yet (only
    founder-created ventures get one automatically)."""
    _require_admin(request)
    if not ChecklistItem.objects.filter(org=request.org).exists():
        from .genesis import seed_genesis

        seed_genesis(request.org)
        messages.success(request, "The path is ready. Start anywhere.")
    return redirect("orgs:dashboard", org_slug=request.org.slug)


@login_required
def onboarding(request):
    """One-flow org setup: create Org + ValuationConfig + admin Membership, land on Members."""
    if request.method == "POST":
        form = OnboardingForm(request.POST)
        if form.is_valid():
            org = form.save(request.user)
            messages.success(
                request,
                f"{org.display_name} is ready. Now the important part: invite your members.",
            )
            return redirect("orgs:members", org_slug=org.slug)
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


def _invite_share_link(request, invite):
    """The one shareable URL for an invite — doorway page or direct accept."""
    if invite.doorway and settings.DOORWAY_BASE_URL:
        return f"{settings.DOORWAY_BASE_URL}{invite.code}/"
    return request.build_absolute_uri(reverse("orgs:accept_invite", kwargs={"code": invite.code}))


@login_required
def members(request, org_slug):
    """Admin UI: list members with role + rate controls, org-wide rate, and an invite form."""
    _require_admin(request)
    memberships = (
        Membership.objects.filter(org=request.org)
        .select_related("user", "org")
        .order_by("user__email")
    )
    invites = list(Invite.objects.filter(org=request.org).order_by("-created_at"))
    for invite in invites:
        # Live links stay copyable from the ledger; dead ones show none.
        invite.share_link = _invite_share_link(request, invite) if invite.can_accept else ""
    return render(
        request,
        "orgs/members.html",
        {
            "memberships": memberships,
            "roles": MembershipRole.choices,
            "invite_form": InviteForm(),
            "invites": invites,
            "doorway_enabled": bool(settings.DOORWAY_BASE_URL),
            "rate_form": OrgRateForm(
                initial={"default_hourly_rate": request.org.default_hourly_rate}
            ),
        },
    )


@login_required
@require_POST
def invite_create(request, org_slug):
    """
    Admin mints a magic-link invite. Doorway invites link to the public commitment page
    (which resolves the code via the S2S API); direct invites link straight to accept.
    """
    _require_admin(request)
    form = InviteForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please correct the invite details.")
        return redirect("orgs:members", org_slug=request.org.slug)

    data = form.cleaned_data
    invite = Invite.objects.create(
        org=request.org,
        role=data["role"],
        audience=data["audience"],
        name=data.get("name", ""),
        email=data.get("email", ""),
        link=data.get("link", ""),
        image_url=data.get("image_url", ""),
        venture_name=data.get("venture_name", ""),
        venture_url=data.get("venture_url", ""),
        drafted_statement=data.get("drafted_statement", ""),
        drafted_social_post=data.get("drafted_social_post", ""),
        doorway=bool(settings.DOORWAY_BASE_URL),  # one flow: doorway whenever configured
        created_by=request.user,
    )
    messages.success(
        request,
        f"Invite minted for {invite.name or invite.email or 'your invitee'} — "
        "copy the link from the invites list below.",
    )
    return redirect("orgs:members", org_slug=request.org.slug)


@login_required
@require_POST
def invite_revoke(request, org_slug, invite_id):
    """Admin kills a live invite link. Accepted invites are past revoking."""
    _require_admin(request)
    invite = Invite.objects.filter(org=request.org, id=invite_id).first()
    if invite is None:
        messages.error(request, "That invite was not found.")
    elif invite.status == InviteStatus.ACCEPTED:
        messages.error(request, "That invite was already accepted — remove the member instead.")
    else:
        invite.mark_revoked()
        messages.success(
            request, f"Invite for {invite.name or invite.email or invite.code} revoked."
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


def _finish_accept(request, invite, user):
    """Join + land: the shared tail of every accept path."""
    try:
        membership, venture_org = accept_invite_for_user(invite, user)
    except InviteError as exc:
        messages.error(request, str(exc))
        return redirect("orgs:landing")
    request.session.pop(SESSION_KEY, None)
    front_door = cohort_front_door_url(venture_org or membership.org)
    if front_door:
        # Cohort deployment: land on the dash's connect route. No django message —
        # it would only render on a later GovKit page, out of context.
        return redirect(front_door)
    if venture_org is not None:
        messages.success(
            request,
            f"{venture_org.display_name} is set up. Start anywhere on the checklist.",
        )
        return redirect("orgs:dashboard", org_slug=venture_org.slug)
    messages.success(request, f"You have joined {membership.org.display_name}.")
    return redirect("orgs:dashboard", org_slug=membership.org.slug)


def accept_invite(request, code):
    """
    Land here from a magic link (directly, or via the doorway after commit). Signed-in
    visitors join immediately. Anonymous visitors get the door: the invite code is a
    bearer capability (see apps/orgs/invites.py), so one button creates their account
    and membership — zero friction, we trust the inviter. Existing sign-ins remain a
    side path (code stashed in session; consume_pending_invite finishes after login).
    The one hard rule: link possession never signs you into an EXISTING account.
    """
    from django.contrib.auth import get_user_model, login
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    try:
        invite = get_invite_for_accept(code)  # validate before doing anything
    except InviteError as exc:
        messages.error(request, str(exc))
        return redirect("orgs:landing")

    if request.user.is_authenticated:
        return _finish_accept(request, invite, request.user)

    # Anonymous: stash the code so the "use my existing sign-in" side door (or any
    # OAuth button) completes the join right after login.
    request.session[SESSION_KEY] = code
    door_context = {
        "invite": invite,
        "org": invite.org,
        "needs_email": not invite.email,
        "linkedtrust_configured": bool(settings.LINKEDTRUST_CLIENT_ID),
        "google_configured": bool(settings.GOOGLE_OAUTH_CLIENT_ID),
    }

    if request.method == "POST":
        User = get_user_model()
        email = (invite.email or request.POST.get("email", "")).strip()
        if not email:
            door_context["error"] = "Enter your email — it becomes your sign-in here."
            return render(request, "orgs/invite_door.html", door_context, status=400)
        try:
            validate_email(email)
        except ValidationError:
            door_context["error"] = "That email doesn't look right."
            door_context["email_value"] = email
            return render(request, "orgs/invite_door.html", door_context, status=400)

        existing = User.objects.filter(email__iexact=email).first()
        if existing is not None:
            # Possession of the link must never unlock an account that already exists.
            messages.info(
                request,
                "That email already has an account here — sign in and you're in.",
            )
            return redirect("accounts:login")

        user = User.objects.create_user(email=email, display_name=invite.name)
        login(request, user)  # sole backend: ModelBackend
        return _finish_accept(request, invite, user)

    return render(request, "orgs/invite_door.html", door_context)
