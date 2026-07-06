"""
Votes — live work-weighted meeting votes (server-rendered, HTMX for phone voting).

Flow: index (create + live/history lists) -> detail (a member votes from their phone) ->
results (weighted breakdown + raw counts). Creating/opening/closing is gated to
stewards/admins; casting a ballot is open to any member. Every action also has a DRF
endpoint in api.py; both call the shared services so the logic lives in one place.

These are informal DIRECTION votes, not binding elections — the UI copy says so.
"""

from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.orgs.models import MembershipRole

from . import services
from .forms import CreateVoteForm
from .models import Vote

STEWARD_ROLES = {MembershipRole.STEWARD, MembershipRole.ADMIN}


def _is_steward(request):
    if request.user.is_authenticated and request.user.is_superuser:
        return True
    membership = getattr(request, "membership", None)
    return membership is not None and membership.role in STEWARD_ROLES


def steward_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not _is_steward(request):
            raise PermissionDenied("Steward or admin role required for this action.")
        return view(request, *args, **kwargs)

    return wrapped


def _my_ballot(vote, membership):
    if membership is None:
        return None
    return vote.ballots.filter(membership=membership).first()


@login_required
def index(request, org_slug):
    """Create form (stewards) + lists of live/draft votes and closed history."""
    votes = list(Vote.objects.for_org(request.org).order_by("-opened_at"))
    live = [v for v in votes if v.closed_at is None]
    history = [v for v in votes if v.closed_at is not None]
    return render(
        request,
        "votes/index.html",
        {
            "page_title": "Votes",
            "org_slug": org_slug,
            "live_votes": live,
            "history": history,
            "can_steward": _is_steward(request),
            "form": CreateVoteForm(),
        },
    )


@login_required
@steward_required
@require_POST
def create_vote(request, org_slug):
    """Create a draft vote, then immediately open it (snapshot weights) for a quick meeting."""
    form = CreateVoteForm(request.POST)
    if not form.is_valid():
        votes = list(Vote.objects.for_org(request.org).order_by("-opened_at"))
        return render(
            request,
            "votes/index.html",
            {
                "page_title": "Votes",
                "org_slug": org_slug,
                "live_votes": [v for v in votes if v.closed_at is None],
                "history": [v for v in votes if v.closed_at is not None],
                "can_steward": True,
                "form": form,
            },
        )
    try:
        vote = services.create_vote(
            request.org, form.cleaned_data["question"], form.cleaned_data["options"]
        )
        services.open_vote(vote)
    except services.VoteError as exc:
        messages.warning(request, str(exc))
        return redirect(reverse("votes:index", kwargs={"org_slug": org_slug}))
    messages.success(request, "Vote opened — share the link so people can vote.")
    return redirect(reverse("votes:detail", kwargs={"org_slug": org_slug, "vote_id": vote.pk}))


@login_required
def detail(request, org_slug, vote_id):
    """The phone voting page: options to tap, current standing, steward controls."""
    vote = get_object_or_404(Vote.objects.for_org(request.org), pk=vote_id)
    membership = getattr(request, "membership", None)
    return render(
        request,
        "votes/detail.html",
        {
            "page_title": vote.question,
            "org_slug": org_slug,
            "vote": vote,
            "status": services.vote_status(vote),
            "is_live": services.is_live(vote),
            "my_ballot": _my_ballot(vote, membership),
            "can_vote": membership is not None,
            "can_steward": _is_steward(request),
            "tally": services.tally(vote),
        },
    )


@login_required
@require_POST
def cast(request, org_slug, vote_id):
    """Cast (or replace) the member's ballot. HTMX-friendly: returns the ballot panel."""
    vote = get_object_or_404(Vote.objects.for_org(request.org), pk=vote_id)
    membership = getattr(request, "membership", None)
    if membership is None:
        raise PermissionDenied("Only members of this org can vote.")
    choice = request.POST.get("choice", "")
    error = None
    try:
        services.cast_ballot(vote, membership, choice)
    except services.VoteError as exc:
        error = str(exc)
    context = {
        "org_slug": org_slug,
        "vote": vote,
        "is_live": services.is_live(vote),
        "my_ballot": _my_ballot(vote, membership),
        "can_vote": True,
        "error": error,
    }
    if request.headers.get("HX-Request"):
        return render(request, "votes/_ballot_panel.html", context)
    if error:
        messages.warning(request, error)
    return redirect(reverse("votes:detail", kwargs={"org_slug": org_slug, "vote_id": vote.pk}))


@login_required
@steward_required
@require_POST
def close_vote(request, org_slug, vote_id):
    """Close a live vote, then show its results."""
    vote = get_object_or_404(Vote.objects.for_org(request.org), pk=vote_id)
    try:
        services.close_vote(vote)
    except services.VoteError as exc:
        messages.warning(request, str(exc))
    else:
        messages.success(request, "Vote closed.")
    return redirect(reverse("votes:results", kwargs={"org_slug": org_slug, "vote_id": vote.pk}))


@login_required
def results(request, org_slug, vote_id):
    """Weighted breakdown (from the open-time snapshot) alongside raw one-per-member counts."""
    vote = get_object_or_404(Vote.objects.for_org(request.org), pk=vote_id)
    return render(
        request,
        "votes/results.html",
        {
            "page_title": f"Results — {vote.question}",
            "org_slug": org_slug,
            "vote": vote,
            "tally": services.tally(vote),
            "unit_name": request.org.unit_name,
            "can_steward": _is_steward(request),
            "is_live": services.is_live(vote),
        },
    )
