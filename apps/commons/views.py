"""
Commons views: orgs / ideas / pool — reachable by anyone invited or signed up
(login-gated; no org membership required, no anonymous access).
"""

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import ProfileLink
from apps.orgs.doorway import wall_cards_by_claim
from apps.orgs.models import Invite, InviteKind, InviteStatus, Org

from .mail import notify_venture_interest
from .models import Idea, IdeaInterest, IdeaInterestKind, VentureInterest


@login_required
def orgs_view(request):
    """Every venture in the cohort, with pitch and a raise-your-hand button —
    the places a person in the pool could land.

    The accelerator is listed with the rest: it runs the program AND it is a
    venture of its own, with work, equity and people who can want in (golda
    2026-07-27). It gets extra items on its dash rail, never a smaller list."""
    orgs = Org.objects.annotate(member_count=Count("memberships")).order_by("display_name")
    mine = {i.org_id: i for i in VentureInterest.objects.filter(user=request.user)}
    member_of = set(request.user.memberships.values_list("org_id", flat=True))
    for org in orgs:
        org.my_interest = mine.get(org.id)
        org.is_member = org.id in member_of
    return render(request, "commons/orgs.html", {"orgs": orgs})


@login_required
@require_POST
def venture_interest(request, slug):
    """Raise (or lower) a hand for a venture. Same row the dash feeds read."""
    org = get_object_or_404(Org, slug=slug)
    if org.memberships.filter(user=request.user).exists():
        return redirect("commons:orgs")
    if request.POST.get("withdraw"):
        VentureInterest.objects.filter(
            org=org, user=request.user, responded_at__isnull=True
        ).delete()
        return redirect("commons:orgs")
    note = (request.POST.get("note") or "").strip()
    interest, created = VentureInterest.objects.get_or_create(
        org=org, user=request.user, defaults={"note": note}
    )
    if not created and interest.responded_at is None and note and note != interest.note:
        interest.note = note
        interest.save(update_fields=["note"])
    if created:
        notify_venture_interest(interest)
    return redirect("commons:orgs")


@login_required
def ideas_view(request):
    """Active ideas with who supports and who wants to build each."""
    ideas = (
        Idea.objects.filter(is_active=True)
        .select_related("created_by")
        .annotate(
            support_count=Count("interests", filter=Q(interests__kind=IdeaInterestKind.SUPPORT)),
            build_count=Count("interests", filter=Q(interests__kind=IdeaInterestKind.BUILD)),
        )
        .prefetch_related("interests__user")
    )
    mine = {i.idea_id: i.kind for i in IdeaInterest.objects.filter(user=request.user)}
    for idea in ideas:
        idea.my_kind = mine.get(idea.id, "")
    return render(request, "commons/ideas.html", {"ideas": ideas, "kinds": IdeaInterestKind})


@login_required
@require_POST
def idea_create(request):
    title = (request.POST.get("title") or "").strip()
    pitch = (request.POST.get("pitch") or "").strip()
    if title and pitch:
        Idea.objects.create(title=title, pitch=pitch, created_by=request.user)
    return redirect("commons:ideas")


@login_required
@require_POST
def idea_interest(request, slug):
    """Declare or change interest. Same kind again = withdraw."""
    idea = get_object_or_404(Idea, slug=slug, is_active=True)
    kind = request.POST.get("kind")
    if kind not in IdeaInterestKind.values:
        return redirect("commons:ideas")
    existing = IdeaInterest.objects.filter(idea=idea, user=request.user).first()
    if existing is None:
        IdeaInterest.objects.create(idea=idea, user=request.user, kind=kind)
    elif existing.kind == kind:
        existing.delete()
    else:
        existing.kind = kind
        existing.save(update_fields=["kind"])
    return redirect("commons:ideas")


@login_required
def pool_view(request):
    """People screened into the applicant pool: accepted pool invites, rendered
    with the person's public profile layer (bio + opted-in links).

    Each person also carries their wall card: the skills they listed and the
    address of their page on the doorway. A venture comes here to find someone
    who can do a thing, so the skills are on the card and the whole card is a
    link through to them. The wall being down costs the skills and the link,
    never the page.
    """
    rows = (
        Invite.objects.filter(
            kind=InviteKind.POOL, status=InviteStatus.ACCEPTED, accepted_by__isnull=False
        )
        .select_related("accepted_by")
        .prefetch_related(
            Prefetch(
                "accepted_by__profile_links",
                queryset=ProfileLink.objects.filter(is_public=True),
                to_attr="public_links",
            )
        )
        .order_by("-expires_at")
    )
    # One person, one card. Somebody invited twice holds two accepted invites
    # and was listed twice; the one carrying their card is the one to show.
    invites = {}
    for row in rows:
        held = invites.get(row.accepted_by_id)
        if held is None or (row.committed_claim_id and not held.committed_claim_id):
            invites[row.accepted_by_id] = row
    invites = list(invites.values())

    cards = wall_cards_by_claim([i.committed_claim_id for i in invites])
    person_base = (settings.COHORT_PERSON_URL or "").rstrip("/")
    for invite in invites:
        card = cards.get(invite.committed_claim_id) or {}
        # Their page is addressed by the card they made, so it is known here
        # whether or not the wall answers. It used to come from the wall alone,
        # and a person the wall did not return had no link at all.
        invite.wall_url = card.get("page_url") or (
            f"{person_base}/{invite.committed_claim_id}/"
            if person_base and invite.committed_claim_id
            else ""
        )
        invite.skills = card.get("skills") or []
        # Their card, the way the mentors page draws one: the profile they
        # filled in here wins, and their wall card fills in for everyone who
        # has not. Reading only the profile left the people who wrote their
        # words on the way in looking like they had written nothing.
        invite.face = invite.accepted_by.avatar_url or card.get("image") or ""
        invite.words = invite.accepted_by.bio or card.get("statement") or ""
    return render(request, "commons/pool.html", {"invites": invites})
