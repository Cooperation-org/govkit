"""
The pie's life: setup → launch → lock-in by majority decision.

The contract these tests pin down:

* Before lock-in, a sponsor percent is a share of the STARTING split, re-resolved
  every time the pie is computed — so the order the team enters people in never
  changes the outcome, and drops earned after launch dilute the percent holder.
* The lock-in decision is a work-weighted majority vote. Weight is what a member has
  earned plus their starting balance. Money doesn't vote: sponsor stakes are not in
  the electorate, and their weight moves nothing.
* Locking in writes the resolved value onto percent stakes (percent kept as
  provenance) and moves the org to LOCKED.
* After lock-in, a percent grant MINTS new equity against the live pie — the only
  honest way in once the split is the record.
* The starting split cannot promise out 100% or more in percent stakes.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.orgs.models import ExternalHolder, OpeningBalance, OrgStake, PiePhase
from apps.pie.services import (
    LOCK_NO,
    LOCK_YES,
    cast_lock_ballot,
    compute_pie,
    current_lock_vote,
    lock_progress,
    resolved_stake_values,
    start_lock_vote,
)


@pytest.fixture
def sponsor(db):
    return ExternalHolder.objects.create(slug="backer", display_name="Backer Co")


@pytest.fixture
def venture(org_factory, user_factory, membership_factory):
    """A launched venture: two members, no starting values yet."""
    from apps.orgs.models import MembershipRole

    org = org_factory(slug="venture")
    org.pie_phase = PiePhase.LAUNCHED
    org.save(update_fields=["pie_phase"])
    admin = membership_factory(
        org=org, user=user_factory(email="founder@example.com"), role=MembershipRole.ADMIN
    )
    member = membership_factory(org=org, user=user_factory(email="cofounder@example.com"))
    return org, admin, member


def _grant(org, membership, value):
    OpeningBalance.objects.create(org=org, membership=membership, value=Decimal(value))


# --------------------------------------------------------------------------- #
# Percent stakes resolve against the starting split, whatever the entry order.
# --------------------------------------------------------------------------- #
def test_percent_stake_stays_true_as_people_are_added(venture, sponsor):
    org, admin, member = venture
    stake = OrgStake.objects.create(org=org, holder=sponsor, target_pct=Decimal("10"))

    _grant(org, admin, "60000")
    pie = compute_pie(org)
    backer = next(s for s in pie.slices if s.is_sponsor)
    assert backer.share_pct == Decimal("10.00")

    # A later entry does NOT go stale: the percent re-resolves against the new whole.
    _grant(org, member, "30000")
    pie = compute_pie(org)
    backer = next(s for s in pie.slices if s.is_sponsor)
    assert backer.share_pct == Decimal("10.00")
    # 90,000 fixed / (1 - 0.10) = 100,000 start; the stake is 10,000 of it.
    assert resolved_stake_values(org)[stake.pk] == Decimal("10000.00")


def test_entry_order_does_not_matter(org_factory, user_factory, membership_factory, sponsor):
    """Percent first or percent last — the same facts give the same split."""
    from apps.orgs.models import MembershipRole

    shares = []
    for order in ("pct-first", "pct-last"):
        org = org_factory()
        m = membership_factory(
            org=org, user=user_factory(), role=MembershipRole.ADMIN
        )
        if order == "pct-first":
            OrgStake.objects.create(org=org, holder=sponsor, target_pct=Decimal("25"))
            _grant(org, m, "75000")
        else:
            _grant(org, m, "75000")
            OrgStake.objects.create(org=org, holder=sponsor, target_pct=Decimal("25"))
        pie = compute_pie(org)
        shares.append(
            {("sponsor" if s.is_sponsor else "member"): s.share_pct for s in pie.slices}
        )
    assert shares[0] == shares[1]
    assert shares[0]["sponsor"] == Decimal("25.00")


def test_drops_dilute_a_percent_stake_like_everyone_else(venture, sponsor):
    """The percent is of the START; work earned after launch sits on top."""
    from apps.drops.models import DropLine, DropRun, DropRunState

    org, admin, member = venture
    _grant(org, admin, "45000")
    OrgStake.objects.create(org=org, holder=sponsor, target_pct=Decimal("10"))
    # Start: 45,000 / 0.9 = 50,000 → backer holds 5,000 = 10%.

    run = DropRun.objects.create(org=org, state=DropRunState.APPROVED)
    DropLine.objects.create(
        org=org,
        run=run,
        membership=member,
        computed_value=Decimal("50000"),
        adjustment=Decimal("0"),
        final_value=Decimal("50000"),
    )
    pie = compute_pie(org)
    backer = next(s for s in pie.slices if s.is_sponsor)
    assert pie.total == Decimal("100000.00")
    assert backer.share_pct == Decimal("5.00")  # diluted, no floor


def test_starting_split_cannot_promise_out_everything(venture, sponsor):
    from apps.orgs.forms import SponsorGrantForm

    org, admin, member = venture
    OrgStake.objects.create(org=org, holder=sponsor, target_pct=Decimal("60"))
    form = SponsorGrantForm(
        {"sponsor": sponsor.id, "target_pct": "40"}, org=org
    )
    assert not form.is_valid()


# --------------------------------------------------------------------------- #
# Lock-in by majority decision. Money doesn't vote.
# --------------------------------------------------------------------------- #
def test_majority_of_member_stake_locks_the_pie(venture, sponsor):
    org, admin, member = venture
    _grant(org, admin, "60000")  # 2/3 of member weight
    _grant(org, member, "30000")  # 1/3
    stake = OrgStake.objects.create(org=org, holder=sponsor, target_pct=Decimal("10"))

    lock = start_lock_vote(org)
    # The electorate is members only — the sponsor's money is not in the snapshot.
    assert set(lock.vote.weight_snapshot) == {str(admin.id), str(member.id)}

    lock = cast_lock_ballot(lock, member, LOCK_YES)
    assert not lock.locked  # 1/3 of the stake is not a majority

    lock = cast_lock_ballot(lock, admin, LOCK_YES)
    assert lock.locked  # now past half

    org.refresh_from_db()
    assert org.pie_phase == PiePhase.LOCKED
    assert org.pie_locked_at is not None
    stake.refresh_from_db()
    # The resolved value is written down; the percent stays as provenance.
    assert stake.value == Decimal("10000.00")
    assert stake.target_pct == Decimal("10")


def test_no_majority_means_still_adjustable(venture):
    org, admin, member = venture
    _grant(org, admin, "40000")
    _grant(org, member, "60000")
    lock = start_lock_vote(org)
    lock = cast_lock_ballot(lock, admin, LOCK_YES)  # only 40%
    cast_lock_ballot(lock, member, LOCK_NO)
    org.refresh_from_db()
    assert org.pie_phase == PiePhase.LAUNCHED
    assert current_lock_vote(org) is not None  # still open; team can keep talking
    progress = lock_progress(lock.vote)
    assert progress["majority"] is False


def test_after_lockin_a_percent_grant_mints_and_dilutes(venture, sponsor, client):
    org, admin, member = venture
    _grant(org, admin, "90000")
    lock = start_lock_vote(org)
    cast_lock_ballot(lock, admin, LOCK_YES)
    org.refresh_from_db()
    assert org.pie_phase == PiePhase.LOCKED

    client.force_login(admin.user)
    resp = client.post(
        reverse("orgs:sponsor_grant", kwargs={"org_slug": org.slug}),
        {"sponsor": sponsor.id, "target_pct": "10", "source_note": "late money in"},
    )
    assert resp.status_code == 302
    pie = compute_pie(org)
    backer = next(s for s in pie.slices if s.is_sponsor)
    # Minted: 90,000 * 10/90 = 10,000 new units; the founder diluted to 90%.
    assert backer.share_pct == Decimal("10.00")
    assert pie.total == Decimal("100000.00")
    stake = OrgStake.objects.get(org=org, holder=sponsor)
    assert stake.value == Decimal("10000.00")
    assert stake.target_pct is None  # a mint is a fixed value, not a percent-of-start
