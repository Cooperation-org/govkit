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
toggle — see apps.orgs.api.EmbedSessionAuthentication).
"""

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.urls import path
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.orgs.api import EmbedSessionAuthentication
from apps.orgs.models import Membership, Org

from .mail import notify_venture_interest
from .models import VentureInterest


def _ventures_qs():
    """Every venture on this install: all orgs except the accelerator itself."""
    qs = Org.objects.annotate(member_count=Count("memberships"))
    accel = settings.ACCELERATOR_ORG_SLUG
    if accel:
        qs = qs.exclude(slug=accel)
    return qs.order_by("display_name")


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
        mine = {
            i.org_id: i for i in VentureInterest.objects.filter(user=request.user)
        }
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
                    "pitch": org.pitch,
                    "website": org.website,
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
            return Response(
                {"error": "already a member"}, status=status.HTTP_400_BAD_REQUEST
            )
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


urlpatterns = [
    path("ventures/", VenturesView.as_view(), name="commons-ventures"),
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
        "orgs/<slug:org_slug>/interest/<int:pk>/respond/",
        InterestRespondView.as_view(),
        name="commons-interest-respond",
    ),
]
