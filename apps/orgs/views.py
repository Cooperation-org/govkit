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
from django.db.models import Count, ProtectedError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .forms import (
    GrantValueForm,
    InviteForm,
    MemberUpdateForm,
    OnboardingForm,
    OrgRateForm,
)
from .genesis import MODULES, module_of, modules_for, start_genesis, toggle_item
from .invites import (
    SESSION_KEY,
    InviteError,
    accept_invite_for_user,
    cohort_front_door_url,
    get_invite_for_accept,
)
from .models import (
    Cohort,
    Invite,
    InviteKind,
    InviteStatus,
    Membership,
    MembershipRole,
    OpeningBalance,
    Org,
)


def landing(request):
    """Public landing + org picker. Shows the current user's orgs if logged in."""
    my_orgs = []
    if request.user.is_authenticated:
        my_orgs = (
            Org.objects.all()
            if request.user.is_superuser
            else Org.objects.filter(memberships__user=request.user).distinct()
        )
    return render(request, "orgs/landing.html", {
        "my_orgs": my_orgs,
        "is_accelerator_admin": _is_accelerator_admin(request.user),
    })


def about_org(request, org_slug):
    """Public "About <org>" stub. A non-member who reaches an org's internal
    pages is redirected here (by OrgContextMiddleware) instead of a raw 403 —
    a friendly page with the org's name and a way to ask to join. Exempt from
    the org membership gate (it IS the page non-members land on)."""
    org = get_object_or_404(Org, slug=org_slug)
    apply_url = (
        settings.ORG_APPLY_URL
        or settings.COHORT_POOL_LANDING
        or settings.PUBLIC_BASE_URL
        or reverse("orgs:landing")
    )
    return render(request, "orgs/about.html", {"org": org, "apply_url": apply_url})


about_org.org_context_exempt = True  # non-members must be able to load it


def _is_accelerator_admin(user):
    """True for a superuser, or an admin of the accelerator org (the org whose
    slug is settings.ACCELERATOR_ORG_SLUG). Empty slug => superusers only."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    slug = settings.ACCELERATOR_ORG_SLUG
    if not slug:
        return False
    return Membership.objects.filter(
        org__slug=slug, user=user, role=MembershipRole.ADMIN
    ).exists()


@login_required
def all_teams(request):
    """Cross-org oversight for accelerator admins: every team at a glance.
    Not a superuser tool — an admin of the accelerator org sees all orgs
    without being made a superuser. Others get 403."""
    if not _is_accelerator_admin(request.user):
        raise PermissionDenied("This overview is for accelerator admins.")
    orgs = (
        Org.objects.annotate(member_count=Count("memberships"))
        .order_by("display_name")
    )
    return render(request, "orgs/all_teams.html", {"orgs": orgs})


@login_required
def dashboard(request, org_slug):
    """
    Org home. On a cohort deployment the workers.vc dash IS the org's home
    (golda 2026-07-22: /o/<slug>/ lands on the Dash) — the GovKit tool pages
    stay at their own tab URLs. Standalone GovKit keeps this page. Venture
    orgs get the module checklist; the pie shows once anything is issued.
    """
    from .invites import cohort_front_door_url

    front_door = cohort_front_door_url(request.org)
    if front_door:
        return redirect(front_door)

    from apps.pie.services import compute_pie
    from apps.pie.views import _svg_segments

    pie = compute_pie(request.org)
    return render(
        request,
        "orgs/dashboard.html",
        {
            "member_count": Membership.objects.filter(org=request.org).count(),
            "modules": modules_for(request.org),
            "pie": pie if pie.total > 0 else None,
            "segments": _svg_segments(pie) if pie.total > 0 else [],
        },
    )


@login_required
@require_POST
def checklist_toggle(request, org_slug, item_key):
    """Check/uncheck a genesis item. Any member; records who and when."""
    if request.membership is None and not request.user.is_superuser:
        raise PermissionDenied("Only members may work the checklist.")
    done, _entry = toggle_item(request.org, item_key, request.user)
    if done is None:
        raise Http404("No such checklist item.")
    return redirect(
        f"{reverse('orgs:dashboard', kwargs={'org_slug': request.org.slug})}"
        f"#module-{module_of(item_key)}"
    )


@login_required
@require_POST
def checklist_seed(request, org_slug):
    """Admin starts the path for an org that is not on it yet (only
    founder-created ventures start automatically)."""
    _require_admin(request)
    if request.org.genesis_started_at is None:
        start_genesis(request.org)
        messages.success(request, "The path is ready. Start anywhere.")
    return redirect("orgs:dashboard", org_slug=request.org.slug)


@login_required
def cohort_progress_view(request, cohort_slug):
    """
    Every team in one cohort, as program staff and mentors see it.

    Not org-scoped: the URL carries a cohort, not an org, so the middleware sets
    no request.org and access is decided in apps.orgs.cohorts (staff by
    membership, mentors by their accepted invite — never by a governance role).
    """
    from .cohorts import can_view_cohort, cohort_progress, item_skip_counts

    cohort = get_object_or_404(Cohort, slug=cohort_slug)
    if not can_view_cohort(request.user, cohort):
        raise PermissionDenied("This overview is for the program's staff and mentors.")
    rows = cohort_progress(cohort)
    return render(
        request,
        "orgs/cohort_progress.html",
        {
            "cohort": cohort,
            "rows": rows,
            "module_keys": [key for key, _label, _week, _items in MODULES],
            "modules_meta": [
                {"key": key, "label": label, "week": week} for key, label, week, _i in MODULES
            ],
            "skips": item_skip_counts(cohort),
        },
    )


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
    # Current pie stake per member, so the admin sees the effect of a grant
    # (share %) and each member's granted starting value in the same row.
    from apps.pie.services import compute_pie

    pie = compute_pie(request.org)
    slice_by_member = {s.membership_id: s for s in pie.slices}
    for m in memberships:
        pie_slice = slice_by_member.get(m.id)
        m.share_pct = pie_slice.share_pct if pie_slice else None
        m.opening_total = pie_slice.opening_total if pie_slice else None
    invites = list(Invite.objects.filter(org=request.org).order_by("-created_at"))
    for invite in invites:
        # Live links stay copyable from the ledger; dead ones show none.
        invite.share_link = _invite_share_link(request, invite) if invite.can_accept else ""
    # If we just minted one, surface it directly so the inviter copies its exact link
    # here — not by hunting the ledger below.
    minted_code = request.GET.get("minted")
    minted_invite = next((i for i in invites if i.code == minted_code), None) if minted_code else None
    return render(
        request,
        "orgs/members.html",
        {
            "memberships": memberships,
            "roles": MembershipRole.choices,
            "unit_name": request.org.unit_name,
            "invite_form": InviteForm(),
            "invites": invites,
            "minted_invite": minted_invite,
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
    # BYOV founders own their venture — they land as its admin (create_venture_org
    # makes them admin regardless), so the invite records Admin, not the dropdown default.
    role = MembershipRole.ADMIN if data["kind"] == InviteKind.BYOV else data["role"]
    invite = Invite.objects.create(
        org=request.org,
        role=role,
        audience=data["audience"],
        kind=data["kind"],
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
    if data.get("already_committed"):
        # Special case: skip the attestation entirely. Born committed so the doorway
        # shows the accept step directly — no new claim is written. The org is still
        # provisioned on accept, and login attaches them to their existing account.
        invite.mark_committed(claim_id=data.get("committed_claim_id"))
    skipped = " (attestation skipped — already committed)" if data.get("already_committed") else ""
    messages.success(
        request,
        f"Invite minted for {invite.name or invite.email or 'your invitee'}{skipped} — "
        "here is the link to share.",
    )
    url = reverse("orgs:members", kwargs={"org_slug": request.org.slug})
    return redirect(f"{url}?minted={invite.code}")


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
def invite_delete(request, org_slug, invite_id):
    """Admin permanently removes an invite row — for junk/test invites that
    should not linger in the list. Works on any status (revoking only hides a
    live link; deleting drops the record). Removing the invite never touches a
    membership: accept creates a Membership as its own row, so a member who
    already joined stays a member; this only clears the invite record."""
    _require_admin(request)
    invite = Invite.objects.filter(org=request.org, id=invite_id).first()
    if invite is None:
        messages.error(request, "That invite was not found.")
    else:
        who = invite.name or invite.email or invite.code
        invite.delete()
        messages.success(request, f"Invite for {who} deleted.")
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
def member_grant_value(request, org_slug, membership_id):
    """Admin grants a member a starting stake for work done before the pie —
    e.g. co-founders who already built the thing. Recorded as an OpeningBalance
    (the historical-equity model), so it enters the pie exactly like imported
    equity and adjusts everyone's share proportionally. Additive by design: each
    grant is its own row, so granting again tops the member up."""
    _require_admin(request)
    membership = Membership.objects.filter(org=request.org, id=membership_id).first()
    if membership is None:
        messages.error(request, "That member was not found.")
        return redirect("orgs:members", org_slug=request.org.slug)

    form = GrantValueForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a starting value greater than zero.")
        return redirect("orgs:members", org_slug=request.org.slug)

    OpeningBalance.objects.create(
        org=request.org,
        membership=membership,
        value=form.cleaned_data["value"],
        source_note=form.cleaned_data.get("source_note") or "Starting value (pre-pie work)",
    )
    messages.success(
        request,
        f"Granted {form.cleaned_data['value']} {request.org.unit_name} "
        f"to {membership.user.email} as a starting stake.",
    )
    return redirect("orgs:members", org_slug=request.org.slug)


@login_required
@require_POST
def member_remove(request, org_slug, membership_id):
    """
    Admin removes a member from the org. The user account survives (it is
    global); only the membership goes. Ballots cascade; issued drop lines and
    project splits/payouts PROTECT the membership — earned history is
    immutable, so those members cannot be deleted, only (one day) deactivated.
    """
    _require_admin(request)
    membership = Membership.objects.filter(org=request.org, id=membership_id).first()
    if membership is None:
        messages.error(request, "That member was not found.")
        return redirect("orgs:members", org_slug=request.org.slug)
    if membership.role == MembershipRole.ADMIN and not _has_other_admin(request.org, membership):
        messages.error(request, "An organization must keep at least one admin.")
        return redirect("orgs:members", org_slug=request.org.slug)

    removing_self = membership.user_id == request.user.id
    email = membership.user.email
    try:
        membership.delete()
    except ProtectedError:
        messages.error(
            request,
            f"{email} has earned equity or payouts on the record, so their "
            "membership cannot be deleted — the earnings record is immutable.",
        )
        return redirect("orgs:members", org_slug=request.org.slug)

    messages.success(request, f"{email} removed from {request.org.display_name}.")
    if removing_self and not request.user.is_superuser:
        return redirect("orgs:landing")
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
    if membership is None and venture_org is None:
        # Pool/supporter invite: no org joined AND no venture created. Land on the
        # cohort's pool landing when configured, else GovKit's own landing.
        # NOTE: a BYOV accept returns (None, venture_org) — membership is None but
        # the founder DID get a venture to land on. It must fall through to the
        # front-door below, not be swept into the pool branch (that bug sent
        # founders to the accelerator/pool page instead of their own venture).
        return redirect(settings.COHORT_POOL_LANDING or "orgs:landing")
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

    # A founder bringing their own venture (BYOV) MUST authenticate with LinkedTrust:
    # the per-team Odoo CRM user is provisioned by matching the member's LinkedTrust
    # OIDC `sub`, so an email-only account (no sub) never gets CRM access — no user,
    # no sales team, no pipeline. Force LinkedTrust before any account is made; the
    # code is already stashed above, so consume_pending_invite finishes the venture
    # join (with a sub) after the OIDC round-trip. Only BYOV is gated — ORG / POOL /
    # SUPPORTER invites keep the frictionless email door untouched. Gate also on a
    # configured provider so standalone GovKit (no LinkedTrust) still works.
    if invite.kind == InviteKind.BYOV and settings.LINKEDTRUST_CLIENT_ID:
        return redirect("accounts:linkedtrust_start")

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
