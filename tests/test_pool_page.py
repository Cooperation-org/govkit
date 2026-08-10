"""The applicant pool page: one card per person, and every card a link.

A venture comes here to find somebody, so a name that does not go anywhere is
the page failing at its one job.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.orgs.models import Invite, InviteKind, InviteStatus, MembershipRole

pytestmark = pytest.mark.django_db


@pytest.fixture
def pool(org_factory, user_factory):
    org = org_factory(slug="vc")

    def joined(user, claim_id):
        return Invite.objects.create(
            org=org,
            audience="founder",
            kind=InviteKind.POOL,
            role="member",
            name=user.get_full_name() or user.email,
            email=user.email,
            committed_claim_id=claim_id,
            status=InviteStatus.ACCEPTED,
            accepted_by=user,
            accepted_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=30),
        )

    return org, joined


def test_one_person_invited_twice_is_one_card(client, pool, user_factory, membership_factory):
    org, joined = pool
    jaya = user_factory(email="jaya@example.com")
    joined(jaya, None)
    joined(jaya, 124774)

    admin = user_factory()
    membership_factory(org, admin, role=MembershipRole.ADMIN)
    client.force_login(admin)
    response = client.get("/commons/pool/")

    assert response.status_code == 200
    assert len(response.context["invites"]) == 1
    # The one carrying their card is the one shown, so the link survives.
    assert response.context["invites"][0].committed_claim_id == 124774


def test_a_card_links_even_when_the_wall_does_not_answer(
    client, pool, settings, user_factory, membership_factory
):
    """page_url used to come from the wall alone, so anyone the wall did not
    return had no link — while their page was up the whole time."""
    settings.COHORT_PERSON_URL = "https://front.example/p/"
    org, joined = pool
    ebenezer = user_factory(email="ebenezer@example.com")
    joined(ebenezer, 124770)

    admin = user_factory()
    membership_factory(org, admin, role=MembershipRole.ADMIN)
    client.force_login(admin)
    response = client.get("/commons/pool/")

    assert response.context["invites"][0].wall_url == "https://front.example/p/124770/"
