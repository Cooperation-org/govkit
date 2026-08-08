"""
Being invited is enough: an invite addressed to the email someone signs in with
admits them, even when they never clicked the link.

Christian was invited as a mentor on 2026-08-03, signed in with LinkedTrust on
2026-08-07 without the link, and landed on an empty org picker with no membership
and no way to say who he was. `claim_invite_for_email` is that gap closed
(golda, 2026-08-08).
"""

import pytest

from apps.orgs.invites import claim_invite_for_email
from apps.orgs.models import (
    Invite,
    InviteKind,
    InviteStatus,
    Membership,
    MembershipRole,
)


@pytest.fixture
def org(org_factory):
    return org_factory(slug="vc", display_name="Workers.vc")


def _invite(org, email, **kwargs):
    return Invite.objects.create(
        org=org,
        role=kwargs.pop("role", MembershipRole.MEMBER),
        audience=kwargs.pop("audience", "mentor"),
        name=kwargs.pop("name", "Christian"),
        email=email,
        **kwargs,
    )


class _Request:
    """Just the two attributes claim_invite_for_email reads."""

    def __init__(self, user):
        self.user = user
        self.session = {}


@pytest.mark.django_db
def test_live_invite_for_my_email_makes_me_a_member(org, user_factory):
    invite = _invite(org, "christian@jacobs.io")
    user = user_factory(email="christian@jacobs.io")

    landing = claim_invite_for_email(_Request(user))

    assert Membership.objects.filter(org=org, user=user).exists()
    invite.refresh_from_db()
    assert invite.status == InviteStatus.ACCEPTED
    assert invite.accepted_by == user
    assert landing is not None


@pytest.mark.django_db
def test_email_match_is_case_insensitive(org, user_factory):
    _invite(org, "Christian@Jacobs.io")
    user = user_factory(email="christian@jacobs.io")

    claim_invite_for_email(_Request(user))

    assert Membership.objects.filter(org=org, user=user).exists()


@pytest.mark.django_db
def test_a_committed_invite_counts_the_same_as_a_created_one(org, user_factory):
    _invite(org, "mo@digisage.io", status=InviteStatus.COMMITTED)
    user = user_factory(email="mo@digisage.io")

    claim_invite_for_email(_Request(user))

    assert Membership.objects.filter(org=org, user=user).exists()


@pytest.mark.django_db
def test_no_invite_leaves_the_person_alone(org, user_factory):
    user = user_factory(email="stranger@example.com")

    assert claim_invite_for_email(_Request(user)) is None
    assert not Membership.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_a_revoked_or_accepted_invite_admits_nobody(org, user_factory):
    _invite(org, "gone@example.com", status=InviteStatus.REVOKED)
    _invite(org, "gone@example.com", status=InviteStatus.ACCEPTED)
    user = user_factory(email="gone@example.com")

    assert claim_invite_for_email(_Request(user)) is None
    assert not Membership.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_an_expired_invite_admits_nobody(org, user_factory):
    from datetime import timedelta

    from django.utils import timezone

    invite = _invite(org, "late@example.com")
    Invite.objects.filter(pk=invite.pk).update(expires_at=timezone.now() - timedelta(days=1))
    user = user_factory(email="late@example.com")

    assert claim_invite_for_email(_Request(user)) is None
    assert not Membership.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_a_login_never_mints_a_venture(org, user_factory):
    """BYOV stays a deliberate act — an org is minted when a founder brings a
    venture, never as a side effect of somebody logging in (golda, 2026-07-20)."""
    _invite(
        org,
        "founder@example.com",
        kind=InviteKind.BYOV,
        audience="founder",
        venture_name="Some Venture",
    )
    user = user_factory(email="founder@example.com")

    assert claim_invite_for_email(_Request(user)) is None
    assert not Membership.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_an_existing_member_still_uses_their_link(
    org, org_factory, user_factory, membership_factory
):
    """Someone already in a team joins a second one by clicking, not by signing in."""
    user = user_factory(email="jefferson@richards.plus")
    membership_factory(org=org, user=user, role=MembershipRole.ADMIN)
    other = org_factory(slug="integralmass", display_name="IntegralMass")
    invite = _invite(other, "jefferson@richards.plus")

    assert claim_invite_for_email(_Request(user)) is None
    assert not Membership.objects.filter(org=other, user=user).exists()
    invite.refresh_from_db()
    assert invite.status != InviteStatus.ACCEPTED


@pytest.mark.django_db
def test_a_pool_invite_records_the_applicant_without_an_org(org, user_factory):
    invite = _invite(org, "applicant@example.com", kind=InviteKind.POOL, audience="founder")
    user = user_factory(email="applicant@example.com")

    landing = claim_invite_for_email(_Request(user))

    invite.refresh_from_db()
    assert invite.status == InviteStatus.ACCEPTED
    assert invite.accepted_by == user
    assert not Membership.objects.filter(user=user).exists()
    assert landing is not None


@pytest.mark.django_db
def test_the_newest_invite_wins(org, org_factory, user_factory):
    _invite(org, "two@example.com")
    newer_org = org_factory(slug="healthdocx", display_name="HealthDocx")
    _invite(newer_org, "two@example.com")
    user = user_factory(email="two@example.com")

    claim_invite_for_email(_Request(user))

    assert Membership.objects.filter(org=newer_org, user=user).exists()
    assert not Membership.objects.filter(org=org, user=user).exists()
