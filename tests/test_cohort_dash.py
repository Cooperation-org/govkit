"""
Cohort-dash JSON endpoints (PLAN-cohort-dash.md items 2-4): the genesis-checklist JSON,
the live open-tasks proxy (Taiga adapter MOCKED — no network; ~60s server-side cache),
and the projects portfolio. Access rides OrgContextMiddleware exactly like the other
org-scoped APIs: 403 for an authenticated non-member, a login redirect for an anonymous
browser, 404 for an unknown org.
"""

import urllib.error
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.orgs.genesis import MODULES, seed_genesis
from apps.orgs.models import ChecklistItem
from apps.projects.models import Deal, Payout, Project, ProjectKind, Split
from apps.tasksources.adapters import OpenTaskDTO
from apps.tasksources.models import TaskSourceConfig

# --------------------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def _fresh_cache():
    """The open-tasks proxy caches per org; keep tests independent."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def team(org_factory, user_factory, membership_factory):
    """An org with one plain member (the dash viewer)."""
    org = org_factory()
    user = user_factory()
    membership = membership_factory(org, user)
    return org, user, membership


@pytest.fixture
def outsider(user_factory):
    return user_factory(email="outsider@example.com")


# --------------------------------------------------------------------------- checklist


def test_checklist_shape_for_member(client, team):
    org, user, _ = team
    seed_genesis(org)
    first = ChecklistItem.objects.filter(org=org).order_by("id").first()
    first.done_at = timezone.now()
    first.save(update_fields=["done_at"])

    client.force_login(user)
    resp = client.get(f"/api/v1/orgs/{org.slug}/checklist/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["org_slug"] == org.slug
    assert len(data["modules"]) == len(MODULES)

    first_module = data["modules"][0]
    assert set(first_module) == {"key", "title", "week", "done", "total", "items"}
    assert first_module["key"] == MODULES[0][0]
    assert first_module["title"] == MODULES[0][1]
    assert first_module["week"] == 1
    assert first_module["done"] == 1
    assert first_module["total"] == len(MODULES[0][2])
    assert first_module["items"][0] == {
        "id": first.id,
        "title": first.title,
        "done": True,
    }
    assert first_module["items"][1]["done"] is False


def test_checklist_empty_for_non_venture_org(client, team):
    org, user, _ = team  # no seed_genesis: not a venture org
    client.force_login(user)
    resp = client.get(f"/api/v1/orgs/{org.slug}/checklist/")
    assert resp.status_code == 200
    assert resp.json() == {"org_slug": org.slug, "modules": []}


def test_checklist_forbidden_for_non_member(client, team, outsider):
    org, _, _ = team
    client.force_login(outsider)
    assert client.get(f"/api/v1/orgs/{org.slug}/checklist/").status_code == 403


def test_checklist_anonymous_redirected_to_login(client, team):
    org, _, _ = team
    resp = client.get(f"/api/v1/orgs/{org.slug}/checklist/")
    assert resp.status_code == 302


# --------------------------------------------------------------------------- open tasks


class _FakeAdapter:
    """Stands in for TaigaAdapter — the endpoint must never hit the network in tests."""

    def __init__(self, tasks=None, error=None):
        self.tasks = tasks or []
        self.error = error
        self.calls = 0

    def fetch_open_tasks(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.tasks


def _source(org):
    return TaskSourceConfig.objects.create(org=org, base_url="https://tracker.example/api")


_OPEN_TASK = OpenTaskDTO(
    external_id="123",
    subject="Ship the widget",
    status="In progress",
    external_url="https://tracker.example/us/123",
    assignee_label="jo",
    ref=45,
    project_slug="acme-board",
)


def test_open_tasks_shape_for_member(client, team):
    org, user, _ = team
    _source(org)
    client.force_login(user)
    fake = _FakeAdapter(tasks=[_OPEN_TASK])
    with patch("apps.tasksources.api.get_adapter", return_value=fake):
        resp = client.get(f"/api/v1/tasksources/orgs/{org.slug}/tasks/open/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tasks"] == [
        {
            "external_id": "123",
            "ref": 45,
            "subject": "Ship the widget",
            "assignee_label": "jo",
            "status": "In progress",
            "external_url": "https://tracker.example/us/123",
            "project_slug": "acme-board",
        }
    ]
    assert data["fetched_at"]  # ISO timestamp of the live fetch


def test_open_tasks_cached_between_requests(client, team):
    org, user, _ = team
    _source(org)
    client.force_login(user)
    fake = _FakeAdapter(tasks=[_OPEN_TASK])
    with patch("apps.tasksources.api.get_adapter", return_value=fake):
        first = client.get(f"/api/v1/tasksources/orgs/{org.slug}/tasks/open/")
        second = client.get(f"/api/v1/tasksources/orgs/{org.slug}/tasks/open/")
    assert fake.calls == 1  # second response came from the server-side cache
    assert first.json() == second.json()

    cache.clear()  # TTL expiry -> the next request fetches live again
    with patch("apps.tasksources.api.get_adapter", return_value=fake):
        client.get(f"/api/v1/tasksources/orgs/{org.slug}/tasks/open/")
    assert fake.calls == 2


def test_open_tasks_no_sources_is_empty_not_error(client, team):
    org, user, _ = team
    client.force_login(user)
    resp = client.get(f"/api/v1/tasksources/orgs/{org.slug}/tasks/open/")
    assert resp.status_code == 200
    assert resp.json()["tasks"] == []


def test_open_tasks_tracker_outage_returns_502(client, team):
    org, user, _ = team
    _source(org)
    client.force_login(user)
    fake = _FakeAdapter(error=urllib.error.URLError("tracker down"))
    with patch("apps.tasksources.api.get_adapter", return_value=fake):
        resp = client.get(f"/api/v1/tasksources/orgs/{org.slug}/tasks/open/")
    assert resp.status_code == 502
    # An outage must not poison the cache with an empty answer.
    ok = _FakeAdapter(tasks=[_OPEN_TASK])
    with patch("apps.tasksources.api.get_adapter", return_value=ok):
        resp = client.get(f"/api/v1/tasksources/orgs/{org.slug}/tasks/open/")
    assert resp.status_code == 200
    assert len(resp.json()["tasks"]) == 1


def test_open_tasks_forbidden_for_non_member(client, team, outsider):
    org, _, _ = team
    client.force_login(outsider)
    assert client.get(f"/api/v1/tasksources/orgs/{org.slug}/tasks/open/").status_code == 403


def test_open_tasks_anonymous_redirected_to_login(client, team):
    org, _, _ = team
    assert client.get(f"/api/v1/tasksources/orgs/{org.slug}/tasks/open/").status_code == 302


# --------------------------------------------------------------------------- portfolio


def test_portfolio_shape_for_plain_member(client, team):
    """A plain MEMBER can read the portfolio — reads are not steward-only."""
    org, user, membership = team
    funded = Project.objects.create(org=org, name="Pilot", slug="pilot", kind=ProjectKind.CLIENT)
    deal = Deal.objects.create(org=org, project=funded, budget_total=Decimal("4500"))
    Split.objects.create(org=org, deal=deal, membership=membership, percent=Decimal("100"))
    Payout.objects.create(
        org=org,
        project=funded,
        membership=membership,
        amount=Decimal("1500"),
        paid_on=date(2026, 7, 1),
    )
    Project.objects.create(org=org, name="Docs", slug="docs", kind=ProjectKind.INTERNAL)

    client.force_login(user)
    resp = client.get(f"/api/v1/projects/orgs/{org.slug}/portfolio/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["currency"] == "USD"
    assert data["budget_total"] == "4500.00"
    assert data["paid_total"] == "1500.00"
    by_name = {p["name"]: p for p in data["projects"]}
    assert by_name["Pilot"] == {
        "id": funded.id,
        "name": "Pilot",
        "kind": "client",
        "status": "active",
        "budget_total": "4500.00",
        "paid_total": "1500.00",
        "promised_pct": "100.0",
    }
    # No deal -> budget-derived fields are None, paid still reported.
    assert by_name["Docs"]["budget_total"] is None
    assert by_name["Docs"]["promised_pct"] is None
    assert by_name["Docs"]["paid_total"] == "0.00"


def test_portfolio_empty_org(client, team):
    org, user, _ = team
    client.force_login(user)
    resp = client.get(f"/api/v1/projects/orgs/{org.slug}/portfolio/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["projects"] == []
    assert data["currency"] is None
    assert data["budget_total"] is None
    assert data["paid_total"] == "0.00"


def test_portfolio_forbidden_for_non_member(client, team, outsider):
    org, _, _ = team
    client.force_login(outsider)
    assert client.get(f"/api/v1/projects/orgs/{org.slug}/portfolio/").status_code == 403


def test_portfolio_anonymous_redirected_to_login(client, team):
    org, _, _ = team
    assert client.get(f"/api/v1/projects/orgs/{org.slug}/portfolio/").status_code == 302
