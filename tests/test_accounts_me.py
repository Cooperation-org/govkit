"""GET /api/v1/accounts/me/ — identity + memberships, including the audience that
brought each member in (read from the accepted invite, per apps/orgs/cohorts.py)."""

from apps.orgs.models import Invite, InviteAudience, InviteStatus, MembershipRole

URL = "/api/v1/accounts/me/"


def _membership_by_slug(body, slug):
    return next(m for m in body["memberships"] if m["org_slug"] == slug)


def test_me_membership_carries_accepted_invite_audience(
    client, user_factory, org_factory, membership_factory
):
    user = user_factory()
    org = org_factory(slug="mentored", display_name="Mentored Org")
    membership_factory(org=org, user=user, role=MembershipRole.MEMBER)
    Invite.objects.create(
        org=org,
        role=MembershipRole.MEMBER,
        audience=InviteAudience.MENTOR,
        status=InviteStatus.ACCEPTED,
        accepted_by=user,
    )

    client.force_login(user)
    body = client.get(URL).json()

    assert _membership_by_slug(body, "mentored")["audience"] == "mentor"


def test_me_membership_without_invite_has_null_audience(
    client, user_factory, org_factory, membership_factory
):
    user = user_factory()
    org = org_factory(slug="direct", display_name="Direct Org")
    membership_factory(org=org, user=user, role=MembershipRole.MEMBER)

    client.force_login(user)
    body = client.get(URL).json()

    assert _membership_by_slug(body, "direct")["audience"] is None
