"""How a venture reaches one person on the wall.

The wall carries no address on purpose. A team that wants to work with
somebody has to be able to write to them, so this hands one over — and the
whole point of the test is who it refuses.
"""

import pytest
from django.utils import timezone
from datetime import timedelta

from apps.orgs.models import Invite, MembershipRole


@pytest.fixture
def worker(org_factory):
    org = org_factory(slug="vc")
    Invite.objects.create(
        org=org,
        audience="founder",
        kind="pool",
        role="member",
        name="Mohamed Salah",
        email="mohamed@example.com",
        committed_claim_id=124772,
        expires_at=timezone.now() + timedelta(days=30),
    )
    return org


def _url(claim_id=124772):
    return f"/api/v1/orgs/people/{claim_id}/contact/"


def test_a_stranger_gets_nothing(client, worker):
    assert client.get(_url()).status_code in (401, 403)


def test_someone_in_the_pool_gets_nothing(client, worker, user_factory, membership_factory):
    """A worker is not a venture, so another worker's address is not theirs."""
    user = user_factory()
    membership_factory(worker, user, role=MembershipRole.MEMBER)
    client.force_login(user)
    assert client.get(_url()).status_code == 403


def test_a_venture_gets_the_address(client, worker, user_factory, membership_factory):
    user = user_factory()
    membership_factory(worker, user, role=MembershipRole.ADMIN)
    client.force_login(user)
    response = client.get(_url())
    assert response.status_code == 200
    assert response.json() == {"name": "Mohamed Salah", "email": "mohamed@example.com"}


def test_nobody_by_that_card(client, worker, user_factory, membership_factory):
    user = user_factory()
    membership_factory(worker, user, role=MembershipRole.ADMIN)
    client.force_login(user)
    assert client.get(_url(claim_id=999999)).status_code == 404
