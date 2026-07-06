"""
Votes tests: the snapshot-at-open contract (later earnings don't change a closed vote's
weighted result), one-ballot-per-member with re-vote replacing, weighted vs raw tally,
lifecycle guards, and role gating on both the HTML views and the DRF API.
"""

from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.drops.models import DropLine, DropRun, DropRunState
from apps.orgs.models import MembershipRole
from apps.votes import services
from apps.votes.models import Ballot, Vote


# ------------------------------------------------------------------- helpers / fixtures
def _approved_line(org, membership, value, when=None):
    run = DropRun.objects.create(org=org, state=DropRunState.APPROVED)
    run.approved_at = when or timezone.now()
    run.save(update_fields=["approved_at"])
    line = DropLine(
        org=org,
        run=run,
        membership=membership,
        computed_value=Decimal(value),
        final_value=Decimal(value),
    )
    line.save()
    return line


@pytest.fixture
def org3(org_factory, user_factory, membership_factory):
    """An org with three members carrying distinct weights (100 / 30 / 0)."""
    org = org_factory()
    heavy = membership_factory(org, user_factory())
    light = membership_factory(org, user_factory())
    zero = membership_factory(org, user_factory())
    _approved_line(org, heavy, "100.00")
    _approved_line(org, light, "30.00")
    return org, heavy, light, zero


# --------------------------------------------------------------------------- lifecycle
def test_create_is_draft_then_open_snapshots(org3):
    org, heavy, light, zero = org3
    vote = services.create_vote(org, "Ship it?", ["Yes", "No"])
    assert services.is_draft(vote)
    assert vote.weight_snapshot == {}
    services.open_vote(vote)
    assert services.is_live(vote)
    # Every member is captured, including the zero-weight one.
    assert vote.weight_snapshot == {
        str(heavy.id): "100.00",
        str(light.id): "30.00",
        str(zero.id): "0",
    }


def test_create_requires_two_distinct_options(org_factory):
    org = org_factory()
    with pytest.raises(services.VoteError):
        services.create_vote(org, "Q?", ["only-one"])
    with pytest.raises(services.VoteError):
        services.create_vote(org, "Q?", ["A", "A"])


def test_cannot_open_twice(org3):
    org, *_ = org3
    vote = services.create_vote(org, "Q?", ["A", "B"])
    services.open_vote(vote)
    with pytest.raises(services.VoteError):
        services.open_vote(vote)


def test_cannot_open_with_no_members(org_factory):
    org = org_factory()
    vote = services.create_vote(org, "Q?", ["A", "B"])
    with pytest.raises(services.VoteError):
        services.open_vote(vote)


# ----------------------------------------------------------------------------- ballots
def test_one_ballot_per_member_revote_replaces(org3):
    org, heavy, light, zero = org3
    vote = services.create_vote(org, "Q?", ["A", "B"])
    services.open_vote(vote)
    services.cast_ballot(vote, heavy, "A")
    services.cast_ballot(vote, heavy, "B")  # re-vote replaces
    assert Ballot.objects.filter(vote=vote, membership=heavy).count() == 1
    assert Ballot.objects.get(vote=vote, membership=heavy).choice == "B"


def test_cannot_vote_on_draft_or_closed(org3):
    org, heavy, *_ = org3
    vote = services.create_vote(org, "Q?", ["A", "B"])
    with pytest.raises(services.VoteError):  # draft
        services.cast_ballot(vote, heavy, "A")
    services.open_vote(vote)
    services.close_vote(vote)
    with pytest.raises(services.VoteError):  # closed
        services.cast_ballot(vote, heavy, "A")


def test_invalid_choice_rejected(org3):
    org, heavy, *_ = org3
    vote = services.create_vote(org, "Q?", ["A", "B"])
    services.open_vote(vote)
    with pytest.raises(services.VoteError):
        services.cast_ballot(vote, heavy, "C")


# ------------------------------------------------------------------------------- tally
def test_weighted_vs_raw_tally(org3):
    org, heavy, light, zero = org3
    vote = services.create_vote(org, "Q?", ["A", "B"])
    services.open_vote(vote)
    services.cast_ballot(vote, heavy, "A")  # weight 100
    services.cast_ballot(vote, light, "B")  # weight 30
    services.cast_ballot(vote, zero, "B")  # weight 0 (raw counts, weighted doesn't)
    t = services.tally(vote)
    by_opt = {r.option: r for r in t.results}
    assert by_opt["A"].weighted == Decimal("100.00")
    assert by_opt["A"].raw == 1
    assert by_opt["B"].weighted == Decimal("30.00")
    assert by_opt["B"].raw == 2  # two people, but only 30 weight
    assert t.weighted_total == Decimal("130.00")
    assert t.raw_total == 3
    assert t.winner == "A"  # A wins by weight though B wins the raw headcount


def test_snapshot_freezes_result_against_later_earnings(org3):
    org, heavy, light, zero = org3
    vote = services.create_vote(org, "Q?", ["A", "B"])
    services.open_vote(vote)
    services.cast_ballot(vote, heavy, "A")  # 100
    services.cast_ballot(vote, light, "B")  # 30
    services.close_vote(vote)
    result_before = services.tally(vote).weighted_total
    # light earns a fortune AFTER the vote closed — must not change the tally.
    _approved_line(org, light, "10000.00")
    vote.refresh_from_db()
    t = services.tally(vote)
    by_opt = {r.option: r for r in t.results}
    assert t.weighted_total == result_before == Decimal("130.00")
    assert by_opt["B"].weighted == Decimal("30.00")  # still the snapshot weight
    assert t.winner == "A"


# ------------------------------------------------------------------- HTTP + role gating
def test_member_cannot_create_via_view(client, org_factory, user_factory, membership_factory):
    org = org_factory()
    member = user_factory()
    membership_factory(org, member, role=MembershipRole.MEMBER)
    client.force_login(member)
    resp = client.post(
        reverse("votes:create", kwargs={"org_slug": org.slug}),
        {"question": "Q?", "options": "A\nB"},
    )
    assert resp.status_code == 403


def test_steward_create_and_member_vote_via_views(
    client, org_factory, user_factory, membership_factory
):
    org = org_factory()
    steward = user_factory()
    membership_factory(org, steward, role=MembershipRole.STEWARD)
    member = user_factory()
    m2 = membership_factory(org, member, role=MembershipRole.MEMBER)
    _approved_line(org, m2, "5.00")

    client.force_login(steward)
    resp = client.post(
        reverse("votes:create", kwargs={"org_slug": org.slug}),
        {"question": "Ship?", "options": "Yes\nNo"},
    )
    assert resp.status_code == 302  # -> detail
    vote = Vote.objects.for_org(org).get()
    assert services.is_live(vote)

    # Member votes from their phone.
    client.force_login(member)
    resp = client.post(
        reverse("votes:cast", kwargs={"org_slug": org.slug, "vote_id": vote.pk}),
        {"choice": "Yes"},
    )
    assert resp.status_code == 302
    assert Ballot.objects.get(vote=vote, membership=m2).choice == "Yes"


def test_api_full_flow_and_role_gate(client, org_factory, user_factory, membership_factory):
    org = org_factory()
    steward = user_factory()
    membership_factory(org, steward, role=MembershipRole.STEWARD)
    member = user_factory()
    m2 = membership_factory(org, member, role=MembershipRole.MEMBER)
    _approved_line(org, m2, "40.00")

    base = f"/api/v1/votes/orgs/{org.slug}/votes/"

    # Member cannot create.
    client.force_login(member)
    assert (
        client.post(
            base, data={"question": "Q?", "options": ["A", "B"]}, content_type="application/json"
        ).status_code
        == 403
    )

    # Steward creates + opens.
    client.force_login(steward)
    resp = client.post(
        base, data={"question": "Q?", "options": ["A", "B"]}, content_type="application/json"
    )
    assert resp.status_code == 201
    vote_id = resp.json()["id"]
    assert resp.json()["status"] == "draft"
    resp = client.post(f"{base}{vote_id}/open/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "live"

    # Member casts a ballot via API and gets a tally back.
    client.force_login(member)
    resp = client.post(
        f"{base}{vote_id}/vote/", data={"choice": "A"}, content_type="application/json"
    )
    assert resp.status_code == 200
    assert resp.json()["raw_total"] == 1

    # Member cannot close; steward can.
    assert client.post(f"{base}{vote_id}/close/").status_code == 403
    client.force_login(steward)
    assert client.post(f"{base}{vote_id}/close/").status_code == 200

    # Tally endpoint reflects the weighted result.
    resp = client.get(f"{base}{vote_id}/tally/")
    assert resp.status_code == 200
    assert resp.json()["weighted_total"] == "40.00"
