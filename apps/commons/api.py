"""
Commons JSON API — ventures a signed-in person can browse and express interest in,
plus the per-org interest feed that venture members answer from their dash.

Mount (config/urls.py): path("api/v1/commons/", include("apps.commons.api")).

URL kwargs matter here: the browse/express endpoints use `venture_slug` ON PURPOSE
so OrgContextMiddleware (which keys on `org_slug`) does not bounce non-members —
expressing interest is exactly what a non-member does. The member-side feed
endpoints use `org_slug` so the middleware enforces membership for free.

Reads/writes ride the member's own session from the workers.vc dash (CORS
allowlist + the X-Govkit-Embed preflight gate, same contract as the checklist
toggle — see apps.orgs.embed_auth).
"""

import json
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db.models import Count
from django.http import JsonResponse
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.orgs.embed_auth import EmbedSessionAuthentication
from apps.orgs.models import Membership, MembershipRole, Org
from apps.orgs.s2s import authorized as s2s_authorized

from .mail import confirm_sponsor_pledge, notify_sponsor_pledge, notify_venture_interest
from .models import SponsorPledge, VentureInterest


def _ventures_qs():
    """Every venture on this install — the accelerator included.

    It raises money, it has a business model, it has people who can want in.
    Running the cohort is a view it ALSO has, not a reason to leave it off the
    list people can back or join (golda 2026-07-27)."""
    return Org.objects.annotate(member_count=Count("memberships")).order_by("display_name")


def _is_org_admin(user, org):
    """Runs this org: its admin, an accelerator admin, or a superuser."""
    from apps.orgs.views import _is_accelerator_admin

    if not user.is_authenticated:
        return False
    if _is_accelerator_admin(user):
        return True
    return Membership.objects.filter(org=org, user=user, role=MembershipRole.ADMIN).exists()


def _interest_payload(i, include_person=False):
    row = {
        "id": i.id,
        "org_slug": i.org.slug,
        "org_name": i.org.display_name,
        "note": i.note,
        "created_at": i.created_at.isoformat(),
        "responded_at": i.responded_at.isoformat() if i.responded_at else None,
    }
    if include_person:
        row["person"] = {
            "display_name": i.user.get_full_name() or i.user.email,
            "email": i.user.email,
            "avatar_url": i.user.avatar_url,
            "bio": i.user.bio,
        }
    return row


class VenturesView(APIView):
    """The venture cards: pitch, site, size, and where I stand with each."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        mine = {i.org_id: i for i in VentureInterest.objects.filter(user=request.user)}
        member_of = set(
            Membership.objects.filter(user=request.user).values_list("org_id", flat=True)
        )
        ventures = []
        for org in _ventures_qs():
            my = mine.get(org.id)
            ventures.append(
                {
                    "slug": org.slug,
                    "display_name": org.display_name,
                    "tagline": org.tagline,
                    "pitch": org.pitch,
                    "website": org.website,
                    "logo_url": org.logo_url,
                    "asks": org.asks,
                    "member_count": org.member_count,
                    "is_member": org.id in member_of,
                    "my_interest": (
                        None
                        if my is None
                        else {"answered": my.responded_at is not None, "note": my.note}
                    ),
                }
            )
        return Response({"ventures": ventures})


class VentureInterestView(APIView):
    """POST: I want to join this venture (optional note). DELETE: withdraw.

    Re-POSTing updates the note on a still-unanswered row; an answered row is
    history and stays as it was. Members can't express interest in their own org.
    """

    permission_classes = [IsAuthenticated]
    authentication_classes = [EmbedSessionAuthentication]

    def post(self, request, venture_slug):
        org = _ventures_qs().filter(slug=venture_slug).first()
        if org is None:
            return Response({"error": "no such venture"}, status=status.HTTP_404_NOT_FOUND)
        if Membership.objects.filter(org=org, user=request.user).exists():
            return Response({"error": "already a member"}, status=status.HTTP_400_BAD_REQUEST)
        note = (request.data.get("note") or "").strip()
        interest, created = VentureInterest.objects.get_or_create(
            org=org, user=request.user, defaults={"note": note}
        )
        if not created and interest.responded_at is None and note != interest.note:
            interest.note = note
            interest.save(update_fields=["note"])
        if created:
            notify_venture_interest(interest)
        return Response(
            _interest_payload(interest),
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def delete(self, request, venture_slug):
        deleted, _ = VentureInterest.objects.filter(
            org__slug=venture_slug, user=request.user, responded_at__isnull=True
        ).delete()
        return Response({"withdrawn": bool(deleted)})


class MyInterestView(APIView):
    """The pool person's own side of the feed: where they've raised a hand."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        rows = VentureInterest.objects.filter(user=request.user).select_related("org")
        return Response({"interests": [_interest_payload(i) for i in rows]})


class OrgInterestFeedView(APIView):
    """The venture's waiting list, unanswered first. Members only (middleware)."""

    def get(self, request, org_slug):
        rows = VentureInterest.objects.filter(org=request.org).select_related("org", "user")
        return Response(
            {
                "org_slug": request.org.slug,
                "interests": [_interest_payload(i, include_person=True) for i in rows],
            }
        )


class OrgAttentionView(APIView):
    """The dash rail: everything on THIS org's plate, one typed list.

    Members only (middleware). A venture gets its own waiting list. On the
    accelerator's rail everyone sees who has been joining — the cohort filling
    up is the whole cohort's news, not the admins' (golda 2026-07-27). What
    stays with admins is the work only they can do: every unanswered hand-raise
    across ventures, and walk-ups pending approval at the doorway.

    New attention kinds append here — the item shape (attention.py) is the
    contract, the embed renders any kind.
    """

    def get(self, request, org_slug):
        from . import attention

        items = attention.org_interest_items(request.org)
        # Someone who raised a hand on this team's public join page has no
        # account here yet, so they are a walk-up in the doorway's ledger, not
        # an interest row. They still came for THIS team, and this team is who
        # has to see them.
        items = items + attention.doorway_items(for_venture=request.org.slug)
        if request.org.slug == settings.ACCELERATOR_ORG_SLUG:
            from apps.orgs.views import _is_accelerator_admin

            items = items + attention.invite_accepted_items()
            if _is_accelerator_admin(request.user):
                items = (
                    attention.all_open_interest_items()
                    + attention.doorway_items()
                    + attention.invite_accepted_items()
                )
        # Sponsorship offered to this org, for the people who can answer it. A
        # pledge names a person and a sum they have not given yet, so it is the
        # admins' to see and not the whole team's.
        if _is_org_admin(request.user, request.org):
            items = items + attention.sponsor_pledge_items(request.org)
        # Unanswered first, oldest first — one rule for every kind.
        items.sort(key=lambda i: (i["done"], i["since"]))
        return Response({"org_slug": request.org.slug, "items": items})


class InterestRespondView(APIView):
    """A member marks an interest answered — after actually replying to the person."""

    authentication_classes = [EmbedSessionAuthentication]

    def post(self, request, org_slug, pk):
        if request.membership is None and not request.user.is_superuser:
            raise PermissionDenied("Only members may answer for the venture.")
        interest = VentureInterest.objects.filter(org=request.org, pk=pk).first()
        if interest is None:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        interest.mark_responded(request.user)
        return Response(_interest_payload(interest, include_person=True))


class OpenInterestView(APIView):
    """Every unanswered interest across all ventures, waiting-longest first —
    the accelerator's oversight read (and the feed a claw pass can consume).
    Accelerator-org admins and superusers only."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.orgs.views import _is_accelerator_admin

        if not _is_accelerator_admin(request.user):
            raise PermissionDenied("This overview is for accelerator admins.")
        rows = VentureInterest.objects.filter(responded_at__isnull=True).select_related(
            "org", "user"
        )
        return Response({"interests": [_interest_payload(i, include_person=True) for i in rows]})


# --- Sponsor pledges ---------------------------------------------------------------------
#
# Creation is S2S (plain Django view, shared bearer): the person filling the form
# is on workers.vc and has no account here, so there is no session to ride. The
# doorway renders the form and posts it; this side owns the row.


@csrf_exempt
def sponsor_pledge_create(request, org_slug):
    """Record one offer of sponsorship. Called by the workers.vc doorway.

    Only `name` and `email` are required — someone who wants to give money must
    never be turned away over a field. Everything else is what they chose to say.
    """
    if request.method != "POST":
        return JsonResponse({"error": "method_not_allowed"}, status=405)
    if not s2s_authorized(request):
        return JsonResponse({"error": "unauthorized"}, status=401)
    org = Org.objects.filter(slug=org_slug).first()
    if org is None:
        return JsonResponse({"error": "not_found"}, status=404)
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "bad_json"}, status=400)

    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    if not name or not email:
        return JsonResponse({"error": "name_and_email_required"}, status=400)
    try:
        validate_email(email)
    except ValidationError:
        return JsonResponse({"error": "bad_email"}, status=400)

    kind = SponsorPledge.Kind.IN_KIND if data.get("kind") == "in_kind" else SponsorPledge.Kind.CASH
    amount = None
    raw_amount = data.get("amount")
    if kind == SponsorPledge.Kind.CASH and raw_amount not in (None, ""):
        try:
            amount = Decimal(str(raw_amount).replace(",", "").replace("$", "").strip())
        except (InvalidOperation, ValueError):
            return JsonResponse({"error": "bad_amount"}, status=400)
        if amount <= 0 or amount >= Decimal("100000000"):
            return JsonResponse({"error": "bad_amount"}, status=400)

    pledge = SponsorPledge.objects.create(
        org=org,
        name=name[:200],
        email=email,
        org_name=(data.get("org_name") or "").strip()[:200],
        kind=kind,
        tier=(data.get("tier") or "").strip()[:40],
        amount=amount,
        offer=(data.get("offer") or "").strip(),
        note=(data.get("note") or "").strip(),
        list_publicly=bool(data.get("list_publicly", True)),
        listed_as=(data.get("listed_as") or "").strip()[:200],
    )
    notify_sponsor_pledge(pledge)
    confirm_sponsor_pledge(pledge)
    return JsonResponse({"id": pledge.id, "summary": pledge.summary}, status=201)


# Bearer auth, no session: the org gate must not run (see orgs/middleware.py).
sponsor_pledge_create.org_context_exempt = True


class SponsorPledgeRespondView(APIView):
    """Mark a pledge answered from the rail. The org's admins, or accelerator admins."""

    authentication_classes = [EmbedSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        from apps.orgs.views import _is_accelerator_admin

        pledge = SponsorPledge.objects.filter(pk=pk).select_related("org").first()
        if pledge is None:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        is_admin = Membership.objects.filter(
            org=pledge.org, user=request.user, role=MembershipRole.ADMIN
        ).exists()
        if not (is_admin or _is_accelerator_admin(request.user)):
            raise PermissionDenied("This pledge is for that team's admins.")
        pledge.mark_responded(request.user)
        return Response({"id": pledge.id, "responded_at": pledge.responded_at.isoformat()})


urlpatterns = [
    path("ventures/", VenturesView.as_view(), name="commons-ventures"),
    path(
        "orgs/<slug:org_slug>/sponsor-pledges/",
        sponsor_pledge_create,
        name="commons-sponsor-pledge-create",
    ),
    path(
        "sponsor-pledges/<int:pk>/respond/",
        SponsorPledgeRespondView.as_view(),
        name="commons-sponsor-pledge-respond",
    ),
    path(
        "ventures/<slug:venture_slug>/interest/",
        VentureInterestView.as_view(),
        name="commons-venture-interest",
    ),
    path("interest/mine/", MyInterestView.as_view(), name="commons-my-interest"),
    path("interest/open/", OpenInterestView.as_view(), name="commons-open-interest"),
    path(
        "orgs/<slug:org_slug>/interest/",
        OrgInterestFeedView.as_view(),
        name="commons-org-interest",
    ),
    path(
        "orgs/<slug:org_slug>/attention/",
        OrgAttentionView.as_view(),
        name="commons-org-attention",
    ),
    path(
        "orgs/<slug:org_slug>/interest/<int:pk>/respond/",
        InterestRespondView.as_view(),
        name="commons-interest-respond",
    ),
]
