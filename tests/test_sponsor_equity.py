"""
Sponsor equity: an outside company holding a share of a venture.

The contract, in one line: an invite carries no money terms at all, and what a venture
starts with is set by its own founder, in their own org, after they accept.

What these tests pin down, because getting any of them wrong would be expensive:

* A BYOV invite states no starting value and no sponsor. Accepting one creates the
  venture with an empty pie.
* A BYOV invite always makes the founder an Admin of their venture, whatever role was
  submitted, by every route that mints one.
* Once the founder sets starting shares, the sponsor's share DILUTES. It is not a
  floor, not a fixed percent, not protected.
* The sponsor's share does NOT VOTE. Governance weight comes from memberships only.

No person names anywhere: members are keyed by neutral emails, and the pie labels
them by the short name those emails produce.
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
    WeightWindow,
)
from apps.orgs.weighting import work_weight_map
from apps.pie.services import (
    HOLDER_SPONSOR,
    compute_personal_standing,
    compute_pie,
    value_for_target_share,
)


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
def byov_invite(accel):
    """A founder invite. It names the venture and nothing about what it is worth."""
    org, admin = accel
    return Invite.objects.create(
        org=org,
        audience="founder",
        kind=InviteKind.BYOV,
        name="Founder One",
        email="founder@example.com",
        venture_name="Venture One",
        created_by=admin,
    )


def _accept(invite, user):
    """Accept a BYOV invite and return the venture org it created."""
    _, venture = accept_invite_for_user(invite, user)
    assert venture is not None
    return venture


def _share_by_label(pie):
    """Shares keyed by display label. A member's is their short name, not their email."""
    return {s.member_label: s.share_pct for s in pie.slices}


@pytest.fixture
def founded_venture(byov_invite, user_factory, sponsor):
    """A venture whose founder settled its starting shares: 50/50 of 100,000 slices.

    None of it came from the invite. The founder did it themselves once they were in,
    which is the only place it can be done.
    """
    venture = _accept(byov_invite, user_factory(email="founder@example.com"))
    founder = Membership.objects.get(org=venture)
    OpeningBalance.objects.create(
        org=venture,
        membership=founder,
        value=Decimal("50000.00"),
        source_note="Building it before the pie existed",
    )
    OrgStake.objects.create(
        org=venture,
        holder=sponsor,
        value=Decimal("50000.00"),
        source_note="Cash and the idea",
    )
    return venture


# --------------------------------------------------------------------------- #
# The invite says nothing about money.
# --------------------------------------------------------------------------- #
def test_accepting_a_byov_invite_starts_an_empty_pie(byov_invite, user_factory):
    venture = _accept(byov_invite, user_factory(email="founder@example.com"))

    assert not OpeningBalance.objects.filter(org=venture).exists()
    assert not OrgStake.objects.filter(org=venture).exists()
    pie = compute_pie(venture)
    assert pie.total == Decimal("0")
    assert pie.sponsor_count == 0


def test_the_founder_settles_the_starting_shares_themselves(byov_invite, user_factory, sponsor):
    """The whole flow, through the pages the founder actually uses."""
    venture = _accept(byov_invite, user_factory(email="founder@example.com"))
    founder = Membership.objects.get(org=venture)
    from django.test import Client

    client = Client()
    client.force_login(founder.user)

    # Their own starting amount, for the work that predates the pie.
    resp = client.post(
        reverse(
            "orgs:member_grant_value",
            kwargs={"org_slug": venture.slug, "membership_id": founder.id},
        ),
        {"value": "50000"},
    )
    assert resp.status_code == 302
    # Then half of what that makes the venture, to the company backing it.
    resp = client.post(
        reverse("orgs:sponsor_grant", kwargs={"org_slug": venture.slug}),
        {"sponsor": sponsor.id, "target_pct": "50", "source_note": "Cash and the idea"},
    )
    assert resp.status_code == 302

    pie = compute_pie(venture)
    assert pie.total == Decimal("100000.00")
    assert _share_by_label(pie) == {
        "founder": Decimal("50.00"),
        "Sponsor Co": Decimal("50.00"),
    }


def test_the_pie_board_offers_the_step_and_takes_no_for_an_answer(byov_invite, user_factory):
    venture = _accept(byov_invite, user_factory(email="founder@example.com"))
    founder = Membership.objects.get(org=venture)
    from django.test import Client

    client = Client()
    client.force_login(founder.user)

    page = client.get(reverse("pie:index", kwargs={"org_slug": venture.slug}))
    assert page.context["offer_initial_shares"] is True

    resp = client.post(
        reverse("orgs:initial_shares_done", kwargs={"org_slug": venture.slug}), {"done": "1"}
    )
    assert resp.status_code == 302
    venture.refresh_from_db()
    assert venture.initial_shares_done is True

    page = client.get(reverse("pie:index", kwargs={"org_slug": venture.slug}))
    assert page.context["offer_initial_shares"] is False


def test_a_plain_member_is_not_offered_the_step(founded_venture, user_factory, membership_factory):
    """Only someone who could act on it is asked."""
    from django.test import Client

    joiner = membership_factory(org=founded_venture, user=user_factory(email="joiner@example.com"))
    client = Client()
    client.force_login(joiner.user)
    page = client.get(reverse("pie:index", kwargs={"org_slug": founded_venture.slug}))
    assert page.context["offer_initial_shares"] is False


# --------------------------------------------------------------------------- #
# A founder bringing their own venture owns it.
# --------------------------------------------------------------------------- #
def test_byov_invite_is_always_admin_whatever_was_submitted(accel, client):
    """The rule is on the model, so no route can mint a founder in as a plain member."""
    org, admin = accel
    client.force_login(admin)
    resp = client.post(
        reverse("orgs:invite_create", kwargs={"org_slug": org.slug}),
        {
            "name": "Founder Two",
            "email": "two@example.com",
            "audience": "founder",
            "kind": InviteKind.BYOV,
            "role": MembershipRole.MEMBER,
            "venture_name": "Venture Two",
        },
    )
    assert resp.status_code == 302
    assert Invite.objects.get(email="two@example.com").role == MembershipRole.ADMIN

    # And directly, which is what the S2S API and mint_invite both do.
    direct = Invite.objects.create(
        org=org, kind=InviteKind.BYOV, role=MembershipRole.MEMBER, venture_name="Three"
    )
    assert direct.role == MembershipRole.ADMIN


def test_an_ordinary_invite_keeps_the_role_it_was_given(accel, client):
    org, admin = accel
    client.force_login(admin)
    client.post(
        reverse("orgs:invite_create", kwargs={"org_slug": org.slug}),
        {
            "name": "Member Four",
            "email": "four@example.com",
            "audience": "member",
            "kind": InviteKind.ORG,
            "role": MembershipRole.MEMBER,
        },
    )
    assert Invite.objects.get(email="four@example.com").role == MembershipRole.MEMBER


# --------------------------------------------------------------------------- #
# The part everyone assumes and nobody checks: it dilutes.
# --------------------------------------------------------------------------- #
def test_sponsor_share_dilutes_as_members_earn(founded_venture):
    founder = Membership.objects.get(org=founded_venture)

    # The founder does another 100,000 slices of tracked work, approved.
    run = DropRun.objects.create(org=founded_venture, state=DropRunState.APPROVED)
    DropLine.objects.create(
        org=founded_venture,
        run=run,
        membership=founder,
        computed_value=Decimal("100000.00"),
        adjustment=Decimal("0"),
        final_value=Decimal("100000.00"),
    )

    pie = compute_pie(founded_venture)
    assert pie.total == Decimal("200000.00")
    # 50/50 becomes 75/25. No floor, no protection, no anti-dilution.
    assert _share_by_label(pie) == {
        "founder": Decimal("75.00"),
        "Sponsor Co": Decimal("25.00"),
    }


def test_a_new_person_earning_in_dilutes_both(founded_venture, user_factory, membership_factory):
    joiner = membership_factory(org=founded_venture, user=user_factory(email="joiner@example.com"))

    run = DropRun.objects.create(org=founded_venture, state=DropRunState.APPROVED)
    DropLine.objects.create(
        org=founded_venture,
        run=run,
        membership=joiner,
        computed_value=Decimal("100000.00"),
        adjustment=Decimal("0"),
        final_value=Decimal("100000.00"),
    )

    shares = _share_by_label(compute_pie(founded_venture))
    assert shares == {
        "founder": Decimal("25.00"),
        "Sponsor Co": Decimal("25.00"),
        "joiner": Decimal("50.00"),
    }


# --------------------------------------------------------------------------- #
# Equity, not governance.
# --------------------------------------------------------------------------- #
def test_sponsor_stake_carries_no_voting_weight(founded_venture):
    """The sponsor owns half the pie and none of the vote."""
    founder = Membership.objects.get(org=founded_venture)

    weights = work_weight_map(founded_venture, WeightWindow.ALL_TIME)
    # The electorate is memberships only. The sponsor is not in it at all, so there is
    # no id to give it weight under and no way for its half to reach a ballot.
    assert set(weights) == {founder.id}
    assert weights[founder.id] == Decimal("50000.00")


def test_sponsor_holds_no_membership(founded_venture):
    assert Membership.objects.filter(org=founded_venture).count() == 1
    # And nothing anywhere invented a user account to stand in for the company.
    assert not Membership.objects.filter(user__email__icontains="sponsor").exists()


def test_personal_standing_reports_the_diluted_share(founded_venture):
    founder = Membership.objects.get(org=founded_venture)

    standing = compute_personal_standing(founded_venture, founder)
    # Their own 50,000, measured against a pie that includes the sponsor's half.
    assert standing.realized_total == Decimal("50000.00")
    assert standing.share_pct == Decimal("50.00")


# --------------------------------------------------------------------------- #
# What the founder is shown before they accept.
# --------------------------------------------------------------------------- #
def test_doorway_payload_carries_no_money_terms(byov_invite, client, settings):
    """The doorway states who and what, never a number the inviter decided."""
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
    assert body["venture_name"] == "Venture One"
    for gone in (
        "sponsor_name",
        "sponsor_url",
        "founding_value",
        "sponsor_pct",
        "sponsor_value",
        "founder_value",
    ):
        assert gone not in body


def test_pie_api_marks_the_sponsor_slice(founded_venture, client):
    founder_user = Membership.objects.get(org=founded_venture).user
    client.force_login(founder_user)

    resp = client.get(reverse("pie-summary", kwargs={"org_slug": founded_venture.slug}))
    assert resp.status_code == 200
    body = resp.json()
    assert body["sponsor_count"] == 1
    sponsor_slice = next(s for s in body["slices"] if s["holder_kind"] == HOLDER_SPONSOR)
    # A sponsor has no membership, so callers must be able to see that and not key on it.
    assert sponsor_slice["membership_id"] is None
    assert sponsor_slice["holder_slug"] == "sponsor-co"
    assert len(sponsor_slice["stakes"]) == 1


def test_holder_survives_the_venture_being_deleted(founded_venture, sponsor):
    """Deleting a venture drops its stake rows and leaves the company itself alone."""
    Org.objects.filter(pk=founded_venture.pk).delete()

    assert ExternalHolder.objects.filter(pk=sponsor.pk).exists()
    assert not OrgStake.objects.filter(org_id=founded_venture.pk).exists()


# --------------------------------------------------------------------------- #
# The normal case: settling the share AFTER the venture already exists.
# --------------------------------------------------------------------------- #
@pytest.fixture
def venture_with_work(org_factory, user_factory, membership_factory):
    """A venture already holding 50,000 slices of one member's approved work."""
    org = org_factory(slug="running", display_name="Running Venture")
    member = membership_factory(
        org=org, user=user_factory(email="builder@example.com"), role=MembershipRole.ADMIN
    )
    run = DropRun.objects.create(org=org, state=DropRunState.APPROVED)
    DropLine.objects.create(
        org=org,
        run=run,
        membership=member,
        computed_value=Decimal("50000.00"),
        adjustment=Decimal("0"),
        final_value=Decimal("50000.00"),
    )
    return org, member


def test_target_share_accounts_for_its_own_dilution(venture_with_work):
    """Half of a 50,000 pie is another 50,000, not 25,000. This is the whole point."""
    org, _ = venture_with_work
    assert value_for_target_share(org, Decimal("50")) == Decimal("50000.00")
    # A quarter: v / (50000 + v) = 0.25 => v = 16,666.67.
    assert value_for_target_share(org, Decimal("25")) == Decimal("16666.67")


def test_target_share_refuses_an_empty_pie(org_factory):
    """A percentage of nothing says nothing; the admin must give an amount."""
    with pytest.raises(ValueError):
        value_for_target_share(org_factory(slug="empty"), Decimal("50"))


def test_target_share_refuses_a_hundred_percent(venture_with_work):
    org, _ = venture_with_work
    for bad in (Decimal("0"), Decimal("100"), Decimal("150")):
        with pytest.raises(ValueError):
            value_for_target_share(org, bad)


def test_grant_by_percent_on_a_running_venture(venture_with_work, sponsor, client):
    """The case that actually happens: the venture is built, then the deal is struck."""
    org, member = venture_with_work
    client.force_login(member.user)

    resp = client.post(
        reverse("orgs:sponsor_grant", kwargs={"org_slug": org.slug}),
        {"sponsor": sponsor.id, "target_pct": "50", "source_note": "Cash and the idea"},
    )
    assert resp.status_code == 302

    pie = compute_pie(org)
    assert pie.total == Decimal("100000.00")
    assert _share_by_label(pie) == {
        "builder": Decimal("50.00"),
        "Sponsor Co": Decimal("50.00"),
    }
    assert OrgStake.objects.get(org=org).source_note == "Cash and the idea"


def test_grant_by_amount_works_on_an_empty_pie(
    org_factory, user_factory, membership_factory, sponsor, client
):
    org = org_factory(slug="fresh", display_name="Fresh")
    admin = membership_factory(
        org=org, user=user_factory(email="admin2@example.com"), role=MembershipRole.ADMIN
    )
    client.force_login(admin.user)

    resp = client.post(
        reverse("orgs:sponsor_grant", kwargs={"org_slug": org.slug}),
        {"sponsor": sponsor.id, "value": "1000"},
    )
    assert resp.status_code == 302
    assert OrgStake.objects.get(org=org).value == Decimal("1000.00")


def test_grant_by_percent_on_an_empty_pie_is_refused(
    org_factory, user_factory, membership_factory, sponsor, client
):
    """Refuse rather than guess: nothing is granted and the admin is told why."""
    org = org_factory(slug="nothing-yet", display_name="Nothing Yet")
    admin = membership_factory(
        org=org, user=user_factory(email="admin3@example.com"), role=MembershipRole.ADMIN
    )
    client.force_login(admin.user)

    resp = client.post(
        reverse("orgs:sponsor_grant", kwargs={"org_slug": org.slug}),
        {"sponsor": sponsor.id, "target_pct": "50"},
    )
    assert resp.status_code == 302
    assert not OrgStake.objects.filter(org=org).exists()


def test_granting_again_tops_up_rather_than_replacing(venture_with_work, sponsor, client):
    org, member = venture_with_work
    client.force_login(member.user)
    url = reverse("orgs:sponsor_grant", kwargs={"org_slug": org.slug})

    client.post(url, {"sponsor": sponsor.id, "value": "10000"})
    client.post(url, {"sponsor": sponsor.id, "value": "5000"})

    assert OrgStake.objects.filter(org=org).count() == 2
    sponsor_slice = next(s for s in compute_pie(org).slices if s.is_sponsor)
    assert sponsor_slice.issued_total == Decimal("15000.00")
    assert len(sponsor_slice.stakes) == 2


def test_a_wrong_grant_can_be_removed(venture_with_work, sponsor, client):
    org, member = venture_with_work
    client.force_login(member.user)
    client.post(
        reverse("orgs:sponsor_grant", kwargs={"org_slug": org.slug}),
        {"sponsor": sponsor.id, "value": "999999"},
    )
    stake = OrgStake.objects.get(org=org)

    resp = client.post(
        reverse("orgs:sponsor_stake_remove", kwargs={"org_slug": org.slug, "stake_id": stake.id})
    )
    assert resp.status_code == 302
    assert not OrgStake.objects.filter(org=org).exists()
    # Back to the members holding all of it, as if the grant never happened.
    assert compute_pie(org).total == Decimal("50000.00")


def test_grant_requires_a_share_or_an_amount(venture_with_work, sponsor, client):
    org, member = venture_with_work
    client.force_login(member.user)
    resp = client.post(
        reverse("orgs:sponsor_grant", kwargs={"org_slug": org.slug}), {"sponsor": sponsor.id}
    )
    assert resp.status_code == 302
    assert not OrgStake.objects.filter(org=org).exists()


def test_grant_refuses_both_a_share_and_an_amount(venture_with_work, sponsor, client):
    """Two answers to one question is a mistake, not a preference order."""
    org, member = venture_with_work
    client.force_login(member.user)
    resp = client.post(
        reverse("orgs:sponsor_grant", kwargs={"org_slug": org.slug}),
        {"sponsor": sponsor.id, "target_pct": "50", "value": "10"},
    )
    assert resp.status_code == 302
    assert not OrgStake.objects.filter(org=org).exists()


def test_only_an_admin_can_grant_a_share(
    venture_with_work, sponsor, user_factory, membership_factory, client
):
    org, _ = venture_with_work
    plain = membership_factory(
        org=org, user=user_factory(email="plain@example.com"), role=MembershipRole.MEMBER
    )
    client.force_login(plain.user)
    resp = client.post(
        reverse("orgs:sponsor_grant", kwargs={"org_slug": org.slug}),
        {"sponsor": sponsor.id, "value": "1000"},
    )
    assert resp.status_code == 403
    assert not OrgStake.objects.filter(org=org).exists()
