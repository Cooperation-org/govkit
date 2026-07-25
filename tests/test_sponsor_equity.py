"""
Sponsor equity: an outside company holding a share of a venture.

The contract, in one line: a BYOV invite states a founding split, accepting it writes
two starting rows (the founder's opening balance and the sponsor's stake), and from
then on the venture has an ordinary pie.

What these tests pin down, because getting any of them wrong would be expensive:

* The split lands exactly as offered, and the two rows sum to the founding value.
* The sponsor's share DILUTES. It is not a floor, not a fixed percent, not protected.
* The sponsor's share does NOT VOTE. Governance weight comes from memberships only.
* An unsponsored venture is untouched: no rows, no sponsor, an empty pie as before.

No person names anywhere — members are keyed by neutral emails.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.drops.models import DropLine, DropRun, DropRunState
from apps.orgs.invites import accept_invite_for_user
from apps.orgs.models import (
    ExternalHolder,
    Invite,
    InviteKind,
    Membership,
    MembershipRole,
    OpeningBalance,
    Org,
    OrgStake,
)
from apps.orgs.models import WeightWindow
from apps.orgs.weighting import work_weight_map
from apps.pie.services import HOLDER_SPONSOR, compute_personal_standing, compute_pie


@pytest.fixture
def sponsor(db):
    return ExternalHolder.objects.create(
        slug="sponsor-co",
        display_name="Sponsor Co",
        # The sponsor's own equity lives somewhere else entirely; we only link to it.
        url="https://cap-table.example/sponsor-co",
    )


@pytest.fixture
def accel(org_factory, user_factory, membership_factory):
    org = org_factory(slug="accel", display_name="Accelerator")
    admin = user_factory(email="admin@example.com")
    membership_factory(org=org, user=admin, role=MembershipRole.ADMIN)
    return org, admin


@pytest.fixture
def byov_invite(accel, sponsor):
    """A founder invite offering 50/50 of a 100,000-slice founding pie."""
    org, admin = accel
    return Invite.objects.create(
        org=org,
        role=MembershipRole.ADMIN,
        audience="founder",
        kind=InviteKind.BYOV,
        name="Founder One",
        email="founder@example.com",
        venture_name="Venture One",
        sponsor=sponsor,
        founding_value=Decimal("100000.00"),
        sponsor_pct=Decimal("50.00"),
        created_by=admin,
    )


def _accept(invite, user):
    """Accept a BYOV invite and return the venture org it created."""
    _, venture = accept_invite_for_user(invite, user)
    assert venture is not None
    return venture


def _share_by_label(pie):
    return {s.member_label: s.share_pct for s in pie.slices}


# --------------------------------------------------------------------------- #
# The split as offered.
# --------------------------------------------------------------------------- #
def test_accept_seeds_the_offered_split(byov_invite, user_factory, sponsor):
    venture = _accept(byov_invite, user_factory(email="founder@example.com"))

    opening = OpeningBalance.objects.get(org=venture)
    stake = OrgStake.objects.get(org=venture)
    assert opening.value == Decimal("50000.00")
    assert stake.value == Decimal("50000.00")
    assert stake.holder == sponsor
    # The two starting rows account for the whole founding value, with nothing lost.
    assert opening.value + stake.value == byov_invite.founding_value


def test_pie_reads_the_split_as_fifty_fifty(byov_invite, user_factory):
    venture = _accept(byov_invite, user_factory(email="founder@example.com"))

    pie = compute_pie(venture)
    assert pie.total == Decimal("100000.00")
    assert pie.member_count == 1
    assert pie.sponsor_count == 1
    assert _share_by_label(pie) == {
        "founder@example.com": Decimal("50.00"),
        "Sponsor Co": Decimal("50.00"),
    }


def test_uneven_split_gives_the_remainder_to_the_founder(accel, sponsor, user_factory):
    """A percent that does not divide evenly must not lose or invent slices."""
    org, admin = accel
    invite = Invite.objects.create(
        org=org,
        kind=InviteKind.BYOV,
        audience="founder",
        role=MembershipRole.ADMIN,
        venture_name="Odd Split",
        sponsor=sponsor,
        founding_value=Decimal("10000.00"),
        sponsor_pct=Decimal("33.33"),
        created_by=admin,
    )
    venture = _accept(invite, user_factory(email="odd@example.com"))

    opening = OpeningBalance.objects.get(org=venture)
    stake = OrgStake.objects.get(org=venture)
    assert stake.value == Decimal("3333.00")
    assert opening.value == Decimal("6667.00")
    assert compute_pie(venture).total == Decimal("10000.00")


# --------------------------------------------------------------------------- #
# The part everyone assumes and nobody checks: it dilutes.
# --------------------------------------------------------------------------- #
def test_sponsor_share_dilutes_as_members_earn(byov_invite, user_factory):
    venture = _accept(byov_invite, user_factory(email="founder@example.com"))
    founder = Membership.objects.get(org=venture)

    # The founder does another 100,000 slices of tracked work, approved.
    run = DropRun.objects.create(org=venture, state=DropRunState.APPROVED)
    DropLine.objects.create(
        org=venture,
        run=run,
        membership=founder,
        computed_value=Decimal("100000.00"),
        adjustment=Decimal("0"),
        final_value=Decimal("100000.00"),
    )

    pie = compute_pie(venture)
    assert pie.total == Decimal("200000.00")
    # 50/50 becomes 75/25. No floor, no protection, no anti-dilution.
    assert _share_by_label(pie) == {
        "founder@example.com": Decimal("75.00"),
        "Sponsor Co": Decimal("25.00"),
    }


def test_a_new_person_earning_in_dilutes_both(byov_invite, user_factory, membership_factory):
    venture = _accept(byov_invite, user_factory(email="founder@example.com"))
    joiner = membership_factory(org=venture, user=user_factory(email="joiner@example.com"))

    run = DropRun.objects.create(org=venture, state=DropRunState.APPROVED)
    DropLine.objects.create(
        org=venture,
        run=run,
        membership=joiner,
        computed_value=Decimal("100000.00"),
        adjustment=Decimal("0"),
        final_value=Decimal("100000.00"),
    )

    shares = _share_by_label(compute_pie(venture))
    assert shares == {
        "founder@example.com": Decimal("25.00"),
        "Sponsor Co": Decimal("25.00"),
        "joiner@example.com": Decimal("50.00"),
    }


# --------------------------------------------------------------------------- #
# Equity, not governance.
# --------------------------------------------------------------------------- #
def test_sponsor_stake_carries_no_voting_weight(byov_invite, user_factory):
    """The sponsor owns half the pie and none of the vote."""
    venture = _accept(byov_invite, user_factory(email="founder@example.com"))
    founder = Membership.objects.get(org=venture)

    weights = work_weight_map(venture, WeightWindow.ALL_TIME)
    # The electorate is memberships only. The sponsor is not in it at all, so there is
    # no id to give it weight under and no way for its half to reach a ballot.
    assert set(weights) == {founder.id}
    assert weights[founder.id] == Decimal("50000.00")


def test_sponsor_holds_no_membership(byov_invite, user_factory):
    venture = _accept(byov_invite, user_factory(email="founder@example.com"))
    assert Membership.objects.filter(org=venture).count() == 1
    # And nothing anywhere invented a user account to stand in for the company.
    assert not Membership.objects.filter(user__email__icontains="sponsor").exists()


def test_personal_standing_reports_the_diluted_share(byov_invite, user_factory):
    venture = _accept(byov_invite, user_factory(email="founder@example.com"))
    founder = Membership.objects.get(org=venture)

    standing = compute_personal_standing(venture, founder)
    # Their own 50,000, measured against a pie that includes the sponsor's half.
    assert standing.realized_total == Decimal("50000.00")
    assert standing.share_pct == Decimal("50.00")


# --------------------------------------------------------------------------- #
# The unsponsored venture is exactly as it was.
# --------------------------------------------------------------------------- #
def test_invite_without_a_sponsor_seeds_nothing(accel, user_factory):
    org, admin = accel
    invite = Invite.objects.create(
        org=org,
        kind=InviteKind.BYOV,
        audience="founder",
        role=MembershipRole.ADMIN,
        venture_name="Unsponsored",
        created_by=admin,
    )
    venture = _accept(invite, user_factory(email="solo@example.com"))

    assert not OpeningBalance.objects.filter(org=venture).exists()
    assert not OrgStake.objects.filter(org=venture).exists()
    pie = compute_pie(venture)
    assert pie.total == Decimal("0")
    assert pie.sponsor_count == 0


def test_partial_offer_seeds_nothing_rather_than_guessing(accel, sponsor, user_factory):
    """A sponsor with no numbers is incomplete; it must not invent a value."""
    org, admin = accel
    invite = Invite.objects.create(
        org=org,
        kind=InviteKind.BYOV,
        audience="founder",
        role=MembershipRole.ADMIN,
        venture_name="Half Stated",
        sponsor=sponsor,
        founding_value=Decimal("100000.00"),
        created_by=admin,
    )
    assert invite.seeds_founding_split is False
    venture = _accept(invite, user_factory(email="half@example.com"))
    assert not OrgStake.objects.filter(org=venture).exists()


def test_org_membership_invite_never_seeds_a_split(accel, sponsor, user_factory):
    """Numbers on a non-BYOV invite are inert: joining an org is not founding one."""
    org, admin = accel
    invite = Invite.objects.create(
        org=org,
        kind=InviteKind.ORG,
        audience="founder",
        role=MembershipRole.MEMBER,
        sponsor=sponsor,
        founding_value=Decimal("100000.00"),
        sponsor_pct=Decimal("50.00"),
        created_by=admin,
    )
    membership, venture = accept_invite_for_user(invite, user_factory(email="joins@example.com"))
    assert venture is None
    assert membership is not None
    assert not OrgStake.objects.filter(org=org).exists()


# --------------------------------------------------------------------------- #
# What the founder is shown, and what the admin can mint.
# --------------------------------------------------------------------------- #
def test_doorway_payload_states_the_terms(byov_invite, client, settings):
    """The doorway must be able to show the split BEFORE the founder accepts."""
    settings.GOVKIT_S2S_TOKEN = "test-s2s-secret"
    resp = client.get(
        reverse(
            "s2s_invite_detail",
            kwargs={"org_slug": byov_invite.org.slug, "code": byov_invite.code},
        ),
        HTTP_AUTHORIZATION="Bearer test-s2s-secret",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sponsor_name"] == "Sponsor Co"
    assert body["sponsor_url"] == "https://cap-table.example/sponsor-co"
    assert body["founding_value"] == "100000.00"
    assert body["sponsor_pct"] == "50.00"
    assert body["founder_value"] == "50000.00"
    assert body["sponsor_value"] == "50000.00"


def test_pie_api_marks_the_sponsor_slice(byov_invite, user_factory, client):
    venture = _accept(byov_invite, user_factory(email="founder@example.com"))
    founder_user = Membership.objects.get(org=venture).user
    client.force_login(founder_user)

    resp = client.get(reverse("pie-summary", kwargs={"org_slug": venture.slug}))
    assert resp.status_code == 200
    body = resp.json()
    assert body["sponsor_count"] == 1
    sponsor_slice = next(s for s in body["slices"] if s["holder_kind"] == HOLDER_SPONSOR)
    # A sponsor has no membership, so callers must be able to see that and not key on it.
    assert sponsor_slice["membership_id"] is None
    assert sponsor_slice["holder_slug"] == "sponsor-co"
    assert len(sponsor_slice["stakes"]) == 1


def test_mint_form_rejects_a_half_filled_split(accel, client):
    """Silently seeding nothing is the failure mode worth refusing outright."""
    org, admin = accel
    client.force_login(admin)
    resp = client.post(
        reverse("orgs:invite_create", kwargs={"org_slug": org.slug}),
        {
            "name": "Founder Two",
            "email": "two@example.com",
            "audience": "founder",
            "kind": InviteKind.BYOV,
            "role": MembershipRole.ADMIN,
            "venture_name": "Venture Two",
            "founding_value": "100000",
            # sponsor and sponsor_pct left out
        },
    )
    assert resp.status_code == 302
    assert not Invite.objects.filter(email="two@example.com").exists()


def test_mint_records_the_split_and_prefills_from_org_defaults(accel, sponsor, client):
    org, admin = accel
    org.default_sponsor = sponsor
    org.default_founding_value = Decimal("100000.00")
    org.default_sponsor_pct = Decimal("50.00")
    org.save()
    client.force_login(admin)

    # The form comes up already carrying the standing offer.
    page = client.get(reverse("orgs:members", kwargs={"org_slug": org.slug}))
    form = page.context["invite_form"]
    assert form.initial["sponsor"] == sponsor.id
    assert form.initial["founding_value"] == Decimal("100000.00")
    assert form.initial["sponsor_pct"] == Decimal("50.00")

    resp = client.post(
        reverse("orgs:invite_create", kwargs={"org_slug": org.slug}),
        {
            "name": "Founder Three",
            "email": "three@example.com",
            "audience": "founder",
            "kind": InviteKind.BYOV,
            "role": MembershipRole.ADMIN,
            "venture_name": "Venture Three",
            "sponsor": sponsor.id,
            "founding_value": "100000",
            "sponsor_pct": "50",
        },
    )
    assert resp.status_code == 302
    invite = Invite.objects.get(email="three@example.com")
    assert invite.seeds_founding_split
    assert invite.founder_value == Decimal("50000.00")


def test_changing_the_default_does_not_move_a_minted_invite(byov_invite, accel, sponsor):
    """The invite is the record of the terms, so it must not track the org's defaults."""
    org, _ = accel
    org.default_sponsor_pct = Decimal("10.00")
    org.default_founding_value = Decimal("1.00")
    org.save()

    byov_invite.refresh_from_db()
    assert byov_invite.sponsor_pct == Decimal("50.00")
    assert byov_invite.founding_value == Decimal("100000.00")


def test_holder_survives_the_venture_being_deleted(byov_invite, user_factory, sponsor):
    """Deleting a venture drops its stake rows and leaves the company itself alone."""
    venture = _accept(byov_invite, user_factory(email="founder@example.com"))
    Org.objects.filter(pk=venture.pk).delete()

    assert ExternalHolder.objects.filter(pk=sponsor.pk).exists()
    assert not OrgStake.objects.filter(org_id=venture.pk).exists()
