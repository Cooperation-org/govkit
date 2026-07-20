"""
Commons views: orgs / ideas / pool — reachable by anyone invited or signed up
(login-gated; no org membership required, no anonymous access).
"""

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import ProfileLink
from apps.orgs.models import Invite, InviteKind, InviteStatus, Org

from .models import Idea, IdeaInterest, IdeaInterestKind


@login_required
def orgs_view(request):
    """Every org in the cohort, with size — the places a person could land."""
    orgs = Org.objects.annotate(member_count=Count("memberships")).order_by("display_name")
    return render(request, "commons/orgs.html", {"orgs": orgs})


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
    with the person's public profile layer (bio + opted-in links)."""
    invites = (
        Invite.objects.filter(
            kind=InviteKind.POOL, status=InviteStatus.ACCEPTED, accepted_by__isnull=False
        )
        .select_related("accepted_by")
        .prefetch_related(Prefetch("accepted_by__profile_links",
                                   queryset=ProfileLink.objects.filter(is_public=True),
                                   to_attr="public_links"))
        .order_by("-expires_at")
    )
    return render(request, "commons/pool.html", {"invites": invites})
