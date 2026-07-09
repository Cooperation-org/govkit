"""Onboarding wizard: creates Org + ValuationConfig + admin Membership (UI + API)."""

import pytest
from django.urls import reverse

from apps.orgs.models import (
    Membership,
    MembershipRole,
    OpeningBalance,
    Org,
    ValuationConfig,
    ValuationMode,
)


def _form_data(**over):
    data = {
        "display_name": "Acme Co",
        "slug": "acme",
        "unit_name": "COOK",
        "default_hourly_rate": "50.00",
        "valuation_mode": ValuationMode.HOURS_RATE,
        "at_risk_multiplier_noncash": "1.0",
        "at_risk_multiplier_cash": "1.0",
        "weight_window": "all_time",
        "assignment_budget_period": "weekly",
        "assignment_budget_amount": "",  # blank = unlimited
        "self_assign_cap": "",
        "budget_enforcement": "soft",
    }
    data.update(over)
    return data


@pytest.mark.django_db
def test_onboarding_creates_org_config_and_admin(client, user_factory):
    user = user_factory()
    client.force_login(user)
    resp = client.post(reverse("orgs:onboarding"), _form_data())
    assert resp.status_code == 302

    org = Org.objects.get(slug="acme")
    assert org.display_name == "Acme Co"
    assert org.unit_name == "COOK"
    # lands on Members — inviting people is the important next step
    assert resp["Location"] == reverse("orgs:members", kwargs={"org_slug": "acme"})

    config = ValuationConfig.objects.get(org=org)
    assert config.valuation_mode == ValuationMode.HOURS_RATE
    assert config.assignment_budget_amount is None  # unlimited

    membership = Membership.objects.get(org=org, user=user)
    assert membership.role == MembershipRole.ADMIN
    # default starting point is a fresh pie — no opening balance
    assert not OpeningBalance.objects.filter(org=org).exists()


@pytest.mark.django_db
def test_onboarding_existing_project_records_initial_valuation(client, user_factory):
    """'Existing project' + initial valuation -> founder's opening balance; fresh ignores it."""
    user = user_factory()
    client.force_login(user)
    resp = client.post(
        reverse("orgs:onboarding"),
        _form_data(start_kind="existing", initial_valuation="1200.00"),
    )
    assert resp.status_code == 302
    org = Org.objects.get(slug="acme")
    ob = OpeningBalance.objects.get(org=org)
    assert ob.membership.user == user
    assert str(ob.value) == "1200.00"
    assert "Initial valuation" in ob.source_note

    # fresh start ignores a stray valuation value
    client.post(
        reverse("orgs:onboarding"),
        _form_data(slug="acme2", start_kind="fresh", initial_valuation="900.00"),
    )
    assert not OpeningBalance.objects.filter(org__slug="acme2").exists()


@pytest.mark.django_db
def test_onboarding_rejects_duplicate_slug(client, user_factory, org_factory):
    org_factory(slug="taken")
    user = user_factory()
    client.force_login(user)
    resp = client.post(reverse("orgs:onboarding"), _form_data(slug="taken"))
    assert resp.status_code == 200  # re-rendered with errors
    assert Org.objects.filter(slug="taken").count() == 1


@pytest.mark.django_db
def test_onboarding_requires_login(client):
    resp = client.get(reverse("orgs:onboarding"))
    assert resp.status_code == 302
    assert reverse("accounts:login") in resp["Location"]


@pytest.mark.django_db
def test_onboarding_api_creates_org(client, user_factory):
    user = user_factory()
    client.force_login(user)
    resp = client.post(
        "/api/v1/orgs/orgs/",
        {
            "display_name": "API Org",
            "slug": "api-org",
            "unit_name": "points",
            "valuation_mode": ValuationMode.DIRECT_VALUE,
        },
        content_type="application/json",
    )
    assert resp.status_code == 201, resp.content
    org = Org.objects.get(slug="api-org")
    assert Membership.objects.get(org=org, user=user).role == MembershipRole.ADMIN
    assert ValuationConfig.objects.filter(org=org).exists()
