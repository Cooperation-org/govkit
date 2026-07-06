"""Multitenancy: cross-org access is blocked, and for_org() isolates querysets."""

import pytest
from django.urls import reverse

from apps.drops.models import DropRun


@pytest.mark.django_db
def test_for_org_isolates_querysets(org_factory):
    org_a = org_factory(slug="a")
    org_b = org_factory(slug="b")
    DropRun.objects.create(org=org_a)
    DropRun.objects.create(org=org_b)
    assert DropRun.objects.for_org(org_a).count() == 1
    assert DropRun.objects.for_org(org_b).count() == 1
    assert DropRun.objects.count() == 2


@pytest.mark.django_db
def test_member_can_view_own_org_dashboard(client, org_factory, user_factory, membership_factory):
    org = org_factory(slug="mine")
    user = user_factory()
    membership_factory(org, user)
    client.force_login(user)
    resp = client.get(reverse("orgs:dashboard", kwargs={"org_slug": "mine"}))
    assert resp.status_code == 200
    # Tab nav renders with org-scoped links.
    assert b"Drops" in resp.content


@pytest.mark.django_db
def test_non_member_is_forbidden(client, org_factory, user_factory, membership_factory):
    org_mine = org_factory(slug="mine")
    org_factory(slug="other")  # an org the user is NOT a member of
    user = user_factory()
    membership_factory(org_mine, user)  # member of 'mine' only
    client.force_login(user)
    resp = client.get(reverse("orgs:dashboard", kwargs={"org_slug": "other"}))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_anonymous_redirected_to_login(client, org_factory):
    org_factory(slug="mine")
    resp = client.get(reverse("orgs:dashboard", kwargs={"org_slug": "mine"}))
    assert resp.status_code == 302
    assert "/accounts/login/" in resp["Location"]


@pytest.mark.django_db
def test_unknown_org_404(client, user_factory):
    user = user_factory()
    client.force_login(user)
    resp = client.get(reverse("orgs:dashboard", kwargs={"org_slug": "nope"}))
    assert resp.status_code == 404
