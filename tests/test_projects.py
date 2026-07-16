"""
Projects-tracker tests: the money math (budget / promised splits / paid-out / remaining,
all computed, never stored), deal validation (splits capped at 100%, one row per member,
own-org memberships only), API role gating, and org isolation.
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.orgs.models import MembershipRole
from apps.projects.models import Deal, Payout, Project, ProjectKind, Split
from apps.projects.services import project_summary

# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def project_factory(db):
    counter = {"n": 0}

    def make(org, **kwargs):
        counter["n"] += 1
        return Project.objects.create(
            org=org,
            name=kwargs.pop("name", f"Project {counter['n']}"),
            slug=kwargs.pop("slug", f"project-{counter['n']}"),
            kind=kwargs.pop("kind", ProjectKind.CLIENT),
            **kwargs,
        )

    return make


@pytest.fixture
def team(org_factory, user_factory, membership_factory):
    """An org with a steward and three members (the 40/30/30 scenario)."""
    org = org_factory()
    steward = membership_factory(org, user_factory(), role=MembershipRole.STEWARD)
    members = [membership_factory(org, user_factory()) for _ in range(3)]
    return org, steward, members


def _deal_4000(org, project, members):
    """Golda's canonical example: $4000 budget, 40/30/30 promised."""
    deal = Deal.objects.create(org=org, project=project, budget_total=Decimal("4000"))
    for membership, pct in zip(members, ("40", "30", "30")):
        Split.objects.create(org=org, deal=deal, membership=membership, percent=Decimal(pct))
    return deal


# --------------------------------------------------------------------------- math


def test_summary_answers_the_question(team, project_factory):
    """$4000 budget, $800 paid out, 3 members at 40/30/30 — in one call."""
    org, steward, members = team
    project = project_factory(org)
    _deal_4000(org, project, members)
    Payout.objects.create(
        org=org, project=project, membership=members[0], amount=Decimal("500"), paid_on=date.today()
    )
    Payout.objects.create(
        org=org, project=project, membership=members[1], amount=Decimal("300"), paid_on=date.today()
    )

    s = project_summary(project)
    assert s["budget_total"] == "4000.00"
    assert s["paid_out_total"] == "800.00"
    assert s["budget_remaining"] == "3200.00"
    assert len(s["members"]) == 3
    by_pct = {Decimal(m["percent"]): m for m in s["members"]}
    assert by_pct[Decimal("40")]["promised"] == "1600.00"
    assert by_pct[Decimal("40")]["remaining"] == "1100.00"
    assert by_pct[Decimal("30")]["paid_out"] in ("300.00", "0.00")


def test_summary_without_deal(team, project_factory):
    """Internal/campaign projects have no deal; money fields stay None, payouts still sum."""
    org, _, members = team
    project = project_factory(org, kind=ProjectKind.INTERNAL)
    Payout.objects.create(
        org=org, project=project, membership=members[0], amount=Decimal("50"), paid_on=date.today()
    )
    s = project_summary(project)
    assert s["budget_total"] is None
    assert s["budget_remaining"] is None
    assert s["paid_out_total"] == "50.00"
    assert s["members"][0]["percent"] is None


def test_payout_without_split_is_surfaced(team, project_factory):
    """A payout to someone with no promised share still appears in the member list."""
    org, _, members = team
    project = project_factory(org)
    deal = Deal.objects.create(org=org, project=project, budget_total=Decimal("1000"))
    Split.objects.create(org=org, deal=deal, membership=members[0], percent=Decimal("50"))
    Payout.objects.create(
        org=org, project=project, membership=members[2], amount=Decimal("100"), paid_on=date.today()
    )
    s = project_summary(project)
    names = {m["name"]: m for m in s["members"]}
    assert names[members[2].user.get_username()]["percent"] is None
    assert names[members[2].user.get_username()]["paid_out"] == "100.00"


# --------------------------------------------------------------------------- API


def _api(client, user):
    client.force_login(user)
    return client


def _project_url(org, pk=None, suffix=""):
    base = f"/api/v1/projects/orgs/{org.slug}/projects/"
    if pk is not None:
        base = f"{base}{pk}/{suffix}"
    return base


def test_member_cannot_write_steward_can(team, project_factory, client):
    org, steward, members = team
    _api(client, members[0].user)
    resp = client.post(
        _project_url(org),
        {"name": "New Thing", "slug": "new-thing", "kind": "client"},
        content_type="application/json",
    )
    assert resp.status_code == 403

    _api(client, steward.user)
    resp = client.post(
        _project_url(org),
        {"name": "New Thing", "slug": "new-thing", "kind": "client"},
        content_type="application/json",
    )
    assert resp.status_code == 201


def test_deal_and_payout_via_api(team, project_factory, client):
    org, steward, members = team
    project = project_factory(org)
    _api(client, steward.user)

    resp = client.put(
        _project_url(org, project.pk, "deal/"),
        {
            "budget_total": "4000",
            "splits": [
                {"membership": members[0].pk, "percent": "40"},
                {"membership": members[1].pk, "percent": "30"},
                {"membership": members[2].pk, "percent": "30"},
            ],
        },
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content

    resp = client.post(
        _project_url(org, project.pk, "payouts/"),
        {"membership": members[0].pk, "amount": "800", "paid_on": "2026-07-16"},
        content_type="application/json",
    )
    assert resp.status_code == 201, resp.content
    assert resp.json()["paid_out_total"] == "800.00"
    assert resp.json()["budget_remaining"] == "3200.00"


def test_deal_rejects_over_100_percent(team, project_factory, client):
    org, steward, members = team
    project = project_factory(org)
    _api(client, steward.user)
    resp = client.put(
        _project_url(org, project.pk, "deal/"),
        {
            "budget_total": "4000",
            "splits": [
                {"membership": members[0].pk, "percent": "80"},
                {"membership": members[1].pk, "percent": "30"},
            ],
        },
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert not Deal.objects.filter(project=project).exists()


def test_deal_rejects_foreign_org_membership(
    team, org_factory, user_factory, membership_factory, project_factory, client
):
    org, steward, _ = team
    other_org = org_factory()
    outsider = membership_factory(other_org, user_factory())
    project = project_factory(org)
    _api(client, steward.user)
    resp = client.put(
        _project_url(org, project.pk, "deal/"),
        {"budget_total": "4000", "splits": [{"membership": outsider.pk, "percent": "50"}]},
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_projects_isolated_between_orgs(
    team, org_factory, user_factory, membership_factory, project_factory, client
):
    org, steward, _ = team
    project = project_factory(org)
    other_org = org_factory()
    other_admin = membership_factory(other_org, user_factory(), role=MembershipRole.ADMIN)
    _api(client, other_admin.user)
    resp = client.get(_project_url(other_org))
    assert resp.status_code == 200
    assert all(p["slug"] != project.slug for p in resp.json())
    resp = client.get(_project_url(other_org, project.pk))
    assert resp.status_code == 404


# --------------------------------------------------------------------------- pages


def test_portfolio_page_renders(team, project_factory, client):
    org, steward, members = team
    project = project_factory(org, name="Streetwell")
    _deal_4000(org, project, members)
    _api(client, members[0].user)
    resp = client.get(reverse("projects:index", kwargs={"org_slug": org.slug}))
    assert resp.status_code == 200
    assert b"Streetwell" in resp.content
    resp = client.get(
        reverse("projects:detail", kwargs={"org_slug": org.slug, "slug": project.slug})
    )
    assert resp.status_code == 200
    assert b"4000.00" in resp.content
