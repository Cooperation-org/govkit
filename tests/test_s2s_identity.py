"""
S2S: who a login is, for another server that holds one.

amebo turned away everyone in the workers pool because, from outside a browser,
"no membership anywhere" and "nobody" read the same. This endpoint separates
them: a person in the pool is a real person with an empty membership list, and
a stranger is 404.
"""

import pytest
from django.urls import reverse

from apps.orgs.models import (
    Invite,
    InviteAudience,
    InviteKind,
    InviteStatus,
    MembershipRole,
)

TOKEN = "test-s2s-secret"
SUB = "https://linkedtrust.us/u/ada"


@pytest.fixture
def s2s(settings):
    settings.GOVKIT_S2S_TOKEN = TOKEN
    return {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}


def url(subject, provider="linkedtrust"):
    return reverse("s2s_identity", kwargs={"provider": provider, "subject": subject})


@pytest.fixture
def pool_person(db, user_factory, org_factory):
    """Accepting a pool invite IS the pool state: no membership anywhere."""
    user = user_factory(email="ada@example.com", display_name="Ada Example")
    user.auth_provider = "linkedtrust"
    user.auth_provider_id = SUB
    user.save()
    Invite.objects.create(
        org=org_factory(slug="vc"),
        role=MembershipRole.MEMBER,
        kind=InviteKind.POOL,
        status=InviteStatus.ACCEPTED,
        accepted_by=user,
    )
    return user


def test_a_person_in_the_pool_is_someone(client, s2s, pool_person):
    body = client.get(url(SUB), **s2s).json()

    assert body["pool"] is True
    assert body["memberships"] == []
    assert body["email"] == "ada@example.com"


def test_a_stranger_is_not_found(client, s2s, db):
    assert client.get(url("nobody-at-all"), **s2s).status_code == 404


def test_a_member_is_not_in_the_pool(client, s2s, user_factory, org_factory, membership_factory):
    user = user_factory(email="bo@example.com")
    user.auth_provider = "linkedtrust"
    user.auth_provider_id = "sub-bo"
    user.save()
    org = org_factory(slug="wayfern", display_name="Wayfern")
    membership_factory(org, user, role=MembershipRole.ADMIN)

    body = client.get(url("sub-bo"), **s2s).json()

    assert body["pool"] is False
    assert body["memberships"] == [
        {
            "org_slug": "wayfern",
            "org_name": "Wayfern",
            "role": MembershipRole.ADMIN,
            "audience": None,
        }
    ]


def test_membership_carries_the_audience_that_brought_them_in(
    client, s2s, user_factory, org_factory, membership_factory
):
    """A mentor is a member whose invite said mentor — the same read as
    accounts/me, so both surfaces answer the same thing."""
    user = user_factory()
    user.auth_provider = "linkedtrust"
    user.auth_provider_id = "sub-mentor"
    user.save()
    org = org_factory(slug="vc")
    membership_factory(org, user, role=MembershipRole.MEMBER)
    Invite.objects.create(
        org=org,
        role=MembershipRole.MEMBER,
        audience=InviteAudience.MENTOR,
        status=InviteStatus.ACCEPTED,
        accepted_by=user,
    )

    body = client.get(url("sub-mentor"), **s2s).json()

    assert body["memberships"][0]["audience"] == "mentor"


def test_no_secret_no_answer(client, s2s, pool_person):
    assert client.get(url(SUB)).status_code == 401
    assert client.get(url(SUB), HTTP_AUTHORIZATION="Bearer wrong").status_code == 401


def test_an_install_with_no_secret_is_shut_not_open(client, settings, pool_person):
    settings.GOVKIT_S2S_TOKEN = ""
    assert client.get(url(SUB), HTTP_AUTHORIZATION="Bearer ").status_code == 401


def test_a_deactivated_login_is_nobody(client, s2s, pool_person):
    pool_person.is_active = False
    pool_person.save()
    assert client.get(url(SUB), **s2s).status_code == 404
