"""Landing/org-picker: with exactly one org, skip the picker and go straight to it.

The picker exists to choose among several orgs. A member with one org should never
see a one-item picker — dash.workers.vc must land them on their org. Superusers (who
see every org) keep the picker for cross-org oversight.
"""

import pytest
from django.urls import reverse

from apps.orgs.models import MembershipRole


@pytest.mark.django_db
def test_single_org_redirects_straight_to_it(client, org_factory, user_factory, membership_factory):
    org = org_factory(slug="only")
    user = user_factory()
    membership_factory(org=org, user=user, role=MembershipRole.ADMIN)
    client.force_login(user)

    resp = client.get(reverse("orgs:landing"))
    assert resp.status_code == 302
    assert resp["Location"] == reverse("orgs:dashboard", kwargs={"org_slug": "only"})


@pytest.mark.django_db
def test_two_orgs_still_show_the_picker(client, org_factory, user_factory, membership_factory):
    a = org_factory(slug="a")
    b = org_factory(slug="b")
    user = user_factory()
    membership_factory(org=a, user=user, role=MembershipRole.MEMBER)
    membership_factory(org=b, user=user, role=MembershipRole.MEMBER)
    client.force_login(user)

    resp = client.get(reverse("orgs:landing"))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_no_orgs_shows_landing_not_a_redirect(client, user_factory):
    client.force_login(user_factory())
    resp = client.get(reverse("orgs:landing"))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_superuser_keeps_the_picker(client, org_factory, user_factory):
    org_factory(slug="lonely")
    su = user_factory(email="su@example.com", is_superuser=True, is_staff=True)
    client.force_login(su)
    resp = client.get(reverse("orgs:landing"))
    assert resp.status_code == 200
