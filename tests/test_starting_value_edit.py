"""Setting a starting value to a number, up or down, without a CSV.

Every starting value is a row in an append-only table — OpeningBalance for a member,
OrgStake for a sponsor — and a stake is the SUM of those rows. Nothing in the app
could lower a total except the CSV import in replace mode, which deletes.

What these tests pin down:

* Setting a total appends ONE row for the difference, negative when it comes down.
  The sum lands on the typed number and the earlier rows are still there, unchanged.
* The adjustment row says who changed it, when, and what it was before.
* A sponsor is corrected in the kind their stake is recorded in: an amount for a
  fixed stake, a percent for a share of the starting split.
* LOCKED refuses on the SERVER, not only by hiding the control.
* Only an org admin can do any of it.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.orgs.equity import (
    EquityLocked,
    EquityRefused,
    set_member_starting_total,
    set_sponsor_pct_total,
)
from apps.orgs.models import (
    ExternalHolder,
    MembershipRole,
    OpeningBalance,
    OrgStake,
    PiePhase,
)
from apps.pie.services import compute_pie


@pytest.fixture
def sponsor(db):
    return ExternalHolder.objects.create(slug="backer", display_name="Backer Co")


@pytest.fixture
def team(org_factory, user_factory, membership_factory):
    """An org mid-setup: an admin on 600, a member on 400."""
    org = org_factory(slug="venture", display_name="Venture")
    admin = membership_factory(org, user_factory(email="admin@example.com"), MembershipRole.ADMIN)
    member = membership_factory(org, user_factory(email="member@example.com"))
    OpeningBalance.objects.create(
        org=org, membership=admin, value=Decimal("600"), source_note="built the thing"
    )
    OpeningBalance.objects.create(
        org=org, membership=member, value=Decimal("400"), source_note="put in cash"
    )
    return org, admin, member


def _stake_of(org, label):
    return next(s for s in compute_pie(org).slices if s.member_label == label)


def _set_member(client, org, membership, value, back="members"):
    return client.post(
        reverse(
            "orgs:member_set_starting",
            kwargs={"org_slug": org.slug, "membership_id": membership.id},
        ),
        {"value": value, "back": back},
    )


def _set_sponsor(client, org, holder, **field):
    return client.post(
        reverse(
            "orgs:sponsor_set_starting",
            kwargs={"org_slug": org.slug, "holder_slug": holder.slug},
        ),
        field,
    )


# --------------------------------------------------------------------------- #
# A member's starting value, up and down.
# --------------------------------------------------------------------------- #
def test_lowering_a_starting_value_appends_the_difference(client, team):
    org, admin, member = team
    client.force_login(admin.user)

    _set_member(client, org, member, "150")

    rows = list(OpeningBalance.objects.filter(org=org, membership=member).order_by("id"))
    assert [r.value for r in rows] == [Decimal("400.00"), Decimal("-250.00")]
    # The original row is untouched: its note still says what it was for.
    assert rows[0].source_note == "put in cash"
    assert _stake_of(org, "member").opening_total == Decimal("150.00")


def test_raising_a_starting_value_appends_the_difference(client, team):
    org, admin, member = team
    client.force_login(admin.user)

    _set_member(client, org, member, "1000")

    assert OpeningBalance.objects.filter(org=org, membership=member).count() == 2
    assert _stake_of(org, "member").opening_total == Decimal("1000.00")


def test_the_adjustment_row_says_who_changed_it_and_what_it_was(client, team):
    org, admin, member = team
    client.force_login(admin.user)

    _set_member(client, org, member, "150")

    row = OpeningBalance.objects.filter(org=org, membership=member).latest("id")
    assert row.source_note.startswith("Adjusted by admin ")
    assert row.source_note.endswith(", was 400.00")
    # The row says what it is, so nothing downstream has to read it out of the prose.
    assert row.is_adjustment is True
    assert OpeningBalance.objects.filter(org=org, is_adjustment=False).count() == 2


def test_setting_the_same_number_records_nothing(client, team):
    org, admin, member = team
    client.force_login(admin.user)

    _set_member(client, org, member, "400")

    assert OpeningBalance.objects.filter(org=org, membership=member).count() == 1


def test_a_starting_value_can_be_set_to_zero(client, team):
    org, admin, member = team
    client.force_login(admin.user)

    _set_member(client, org, member, "0")

    assert _stake_of(org, "member").opening_total == Decimal("0.00")
    assert OpeningBalance.objects.filter(org=org, membership=member).count() == 2


def test_a_negative_starting_value_is_refused(client, team):
    org, admin, member = team
    client.force_login(admin.user)

    _set_member(client, org, member, "-50")

    assert OpeningBalance.objects.filter(org=org, membership=member).count() == 1


def test_lowering_one_share_raises_everyone_elses(client, team):
    org, admin, member = team
    client.force_login(admin.user)
    assert _stake_of(org, "admin").share_pct == Decimal("60.00")

    _set_member(client, org, member, "0")

    assert _stake_of(org, "admin").share_pct == Decimal("100.00")


def test_the_edit_lands_back_on_the_page_it_came_from(client, team):
    org, admin, member = team
    client.force_login(admin.user)

    assert _set_member(client, org, member, "100", back="pie")["Location"] == reverse(
        "pie:index", kwargs={"org_slug": org.slug}
    )
    assert _set_member(client, org, member, "200", back="members")["Location"] == reverse(
        "orgs:members", kwargs={"org_slug": org.slug}
    )


# --------------------------------------------------------------------------- #
# A sponsor's stake, in the kind it was recorded in.
# --------------------------------------------------------------------------- #
def test_lowering_a_fixed_sponsor_stake_appends_the_difference(client, team, sponsor):
    org, admin, _member = team
    OrgStake.objects.create(org=org, holder=sponsor, value=Decimal("500"), source_note="cash in")
    client.force_login(admin.user)

    _set_sponsor(client, org, sponsor, value="200")

    rows = list(OrgStake.objects.filter(org=org, holder=sponsor).order_by("id"))
    assert [r.value for r in rows] == [Decimal("500.00"), Decimal("-300.00")]
    assert [r.is_adjustment for r in rows] == [False, True]
    assert rows[1].granted_by == admin.user
    assert _stake_of(org, "Backer Co").issued_total == Decimal("200.00")


def test_a_percent_sponsor_stake_is_corrected_as_a_percent(client, team, sponsor):
    org, admin, _member = team
    OrgStake.objects.create(org=org, holder=sponsor, target_pct=Decimal("20"))
    client.force_login(admin.user)
    # 20% of the starting split: 1000 of member value resolves it to 250 of 1250.
    assert _stake_of(org, "Backer Co").share_pct == Decimal("20.00")

    _set_sponsor(client, org, sponsor, target_pct="10")

    rows = list(OrgStake.objects.filter(org=org, holder=sponsor).order_by("id"))
    assert [r.target_pct for r in rows] == [Decimal("20.00"), Decimal("-10.00")]
    assert [r.is_adjustment for r in rows] == [False, True]
    assert _stake_of(org, "Backer Co").share_pct == Decimal("10.00")


def test_an_adjustment_is_not_shown_as_a_reason_the_sponsor_holds_a_share(client, team, sponsor):
    org, admin, _member = team
    OrgStake.objects.create(
        org=org, holder=sponsor, target_pct=Decimal("20"), source_note="seed funding"
    )
    client.force_login(admin.user)
    _set_sponsor(client, org, sponsor, target_pct="10")

    body = client.get(reverse("orgs:members", kwargs={"org_slug": org.slug})).content.decode()
    assert "seed funding" in body
    assert "Adjusted by" not in body


def test_a_percent_stake_cannot_take_the_whole_starting_split(client, team, sponsor):
    org, admin, _member = team
    other = ExternalHolder.objects.create(slug="other", display_name="Other Co")
    OrgStake.objects.create(org=org, holder=sponsor, target_pct=Decimal("20"))
    OrgStake.objects.create(org=org, holder=other, target_pct=Decimal("30"))
    client.force_login(admin.user)

    _set_sponsor(client, org, sponsor, target_pct="70")

    assert OrgStake.objects.filter(org=org, holder=sponsor).count() == 1


def test_setting_the_amount_leaves_a_percent_stake_alone(client, team, sponsor):
    org, admin, _member = team
    OrgStake.objects.create(org=org, holder=sponsor, target_pct=Decimal("20"))
    client.force_login(admin.user)

    _set_sponsor(client, org, sponsor, value="100")

    assert OrgStake.objects.filter(org=org, holder=sponsor, target_pct=Decimal("20")).exists()
    assert OrgStake.objects.filter(org=org, holder=sponsor, value=Decimal("100.00")).exists()


def test_a_company_holding_nothing_here_cannot_be_set(client, team, sponsor):
    org, admin, _member = team
    client.force_login(admin.user)

    _set_sponsor(client, org, sponsor, value="100")

    assert not OrgStake.objects.filter(org=org, holder=sponsor).exists()


# --------------------------------------------------------------------------- #
# Locked: the control goes AND the server refuses.
# --------------------------------------------------------------------------- #
def test_a_locked_pie_refuses_a_member_edit(client, team):
    org, admin, member = team
    org.pie_phase = PiePhase.LOCKED
    org.save(update_fields=["pie_phase"])
    client.force_login(admin.user)

    _set_member(client, org, member, "1")

    assert OpeningBalance.objects.filter(org=org, membership=member).count() == 1
    assert _stake_of(org, "member").opening_total == Decimal("400.00")


def test_a_locked_pie_refuses_a_sponsor_edit(client, team, sponsor):
    org, admin, _member = team
    OrgStake.objects.create(org=org, holder=sponsor, value=Decimal("500"))
    org.pie_phase = PiePhase.LOCKED
    org.save(update_fields=["pie_phase"])
    client.force_login(admin.user)

    _set_sponsor(client, org, sponsor, value="1")

    assert OrgStake.objects.filter(org=org, holder=sponsor).count() == 1


def test_the_service_refuses_a_locked_pie_on_its_own(team, sponsor):
    org, _admin, member = team
    OrgStake.objects.create(org=org, holder=sponsor, target_pct=Decimal("20"))
    org.pie_phase = PiePhase.LOCKED
    org.save(update_fields=["pie_phase"])

    with pytest.raises(EquityLocked):
        set_member_starting_total(org, member, Decimal("1"))
    with pytest.raises(EquityLocked):
        set_sponsor_pct_total(org, sponsor, Decimal("1"))


def test_the_service_refuses_a_negative_total(team):
    org, _admin, member = team
    with pytest.raises(EquityRefused):
        set_member_starting_total(org, member, Decimal("-1"))


def _member_control(org, membership):
    """The form action that only appears where the control is drawn."""
    return reverse(
        "orgs:member_set_starting",
        kwargs={"org_slug": org.slug, "membership_id": membership.id},
    )


@pytest.mark.parametrize("phase", [PiePhase.SETUP, PiePhase.LAUNCHED])
def test_the_control_is_on_both_pages_while_the_pie_is_adjustable(client, team, phase):
    org, admin, member = team
    org.pie_phase = phase
    org.save(update_fields=["pie_phase"])
    client.force_login(admin.user)

    for page in ("orgs:members", "pie:index"):
        body = client.get(reverse(page, kwargs={"org_slug": org.slug})).content.decode()
        assert _member_control(org, member) in body, page


def test_the_control_is_gone_once_the_pie_is_locked(client, team):
    org, admin, member = team
    org.pie_phase = PiePhase.LOCKED
    org.save(update_fields=["pie_phase"])
    client.force_login(admin.user)

    for page in ("orgs:members", "pie:index"):
        body = client.get(reverse(page, kwargs={"org_slug": org.slug})).content.decode()
        assert _member_control(org, member) not in body, page


# --------------------------------------------------------------------------- #
# Admins only.
# --------------------------------------------------------------------------- #
def test_a_plain_member_cannot_set_a_starting_value(client, team):
    org, _admin, member = team
    client.force_login(member.user)

    assert _set_member(client, org, member, "1").status_code == 403
    assert _stake_of(org, "member").opening_total == Decimal("400.00")


def test_a_plain_member_cannot_set_a_sponsor_stake(client, team, sponsor):
    org, _admin, member = team
    OrgStake.objects.create(org=org, holder=sponsor, value=Decimal("500"))
    client.force_login(member.user)

    assert _set_sponsor(client, org, sponsor, value="1").status_code == 403
    assert OrgStake.objects.filter(org=org, holder=sponsor).count() == 1


def test_a_plain_member_sees_no_control_on_the_pie(client, team):
    org, _admin, member = team
    client.force_login(member.user)

    body = client.get(reverse("pie:index", kwargs={"org_slug": org.slug})).content.decode()
    assert _member_control(org, member) not in body
