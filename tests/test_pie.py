"""
Pie service + page + API tests.

Covers the traceability contract: shares sum to 100% (within rounding), only
APPROVED/issued lines count (open-run lines are pending, excluded from the pie), opening
balances are included, the empty org doesn't divide by zero, provenance drilldown returns
the exact contributing lines/tasks/balances, and personal standing separates pending from
issued. No person names anywhere — members are keyed by neutral emails.
"""

from decimal import Decimal

import pytest

from apps.drops.models import DropLine, DropRun, DropRunState
from apps.orgs.models import MembershipRole, OpeningBalance
from apps.pie.services import compute_personal_standing, compute_pie
from apps.tasksources.models import TaskSourceConfig, TrackedTask

CENT = Decimal("0.01")


# --------------------------------------------------------------------------- #
# Fixtures that build a small, fully-controlled earnings record.
# --------------------------------------------------------------------------- #
@pytest.fixture
def make_run(db):
    def _make(org, state=DropRunState.APPROVED):
        return DropRun.objects.create(org=org, state=state)

    return _make


@pytest.fixture
def make_line(db):
    def _make(org, run, membership, final, computed=None, adjustment=Decimal("0"), reason=""):
        return DropLine.objects.create(
            org=org,
            run=run,
            membership=membership,
            computed_value=computed if computed is not None else final,
            adjustment=adjustment,
            adjustment_reason=reason,
            final_value=final,
        )

    return _make


@pytest.fixture
def make_task(db):
    def _make(org, external_id, subject="", url=""):
        source, _ = TaskSourceConfig.objects.get_or_create(
            org=org, defaults={"base_url": "https://tracker.example/api"}
        )
        return TrackedTask.objects.create(
            org=org, source=source, external_id=external_id, subject=subject, external_url=url
        )

    return _make


def _members(org, org_factory, user_factory, membership_factory, n):
    # Neutral, auto-generated emails (no person names); unique across orgs.
    return [membership_factory(org, user_factory()) for _ in range(n)]


# --------------------------------------------------------------------------- #
# Core aggregation.
# --------------------------------------------------------------------------- #
def _org_with_unit(org_factory, unit):
    org = org_factory()
    org.unit_name = unit
    org.save()
    return org


def test_shares_sum_to_100(org_factory, user_factory, membership_factory, make_run, make_line):
    org = org_factory()
    a, b, c = _members(org, org_factory, user_factory, membership_factory, 3)
    run = make_run(org)
    make_line(org, run, a, Decimal("50.00"))
    make_line(org, run, b, Decimal("30.00"))
    make_line(org, run, c, Decimal("20.00"))

    pie = compute_pie(org)
    assert pie.total == Decimal("100.00")

    # Raw fractions sum to 1 within tiny rounding drift.
    frac_sum = sum((s.share for s in pie.slices), Decimal("0"))
    assert abs(frac_sum - Decimal("1")) < Decimal("0.000001")
    # Display percentages sum to ~100.
    pct_sum = sum((s.share_pct for s in pie.slices), Decimal("0"))
    assert abs(pct_sum - Decimal("100")) < Decimal("0.05")


def test_only_approved_lines_count(
    org_factory, user_factory, membership_factory, make_run, make_line
):
    org = org_factory()
    (a,) = _members(org, org_factory, user_factory, membership_factory, 1)
    approved = make_run(org, state=DropRunState.APPROVED)
    open_run = make_run(org, state=DropRunState.OPEN)
    make_line(org, approved, a, Decimal("40.00"))
    make_line(org, open_run, a, Decimal("999.00"))  # pending — must be excluded

    pie = compute_pie(org)
    assert pie.total == Decimal("40.00")
    assert pie.slices[0].issued_total == Decimal("40.00")
    assert pie.slices[0].drops_total == Decimal("40.00")


def test_opening_balances_included(
    org_factory, user_factory, membership_factory, make_run, make_line
):
    org = org_factory()
    a, b = _members(org, org_factory, user_factory, membership_factory, 2)
    run = make_run(org)
    make_line(org, run, a, Decimal("60.00"))
    OpeningBalance.objects.create(
        org=org, membership=b, value=Decimal("40.00"), source_note="import"
    )

    pie = compute_pie(org)
    assert pie.total == Decimal("100.00")
    by_member = {s.membership_id: s for s in pie.slices}
    assert by_member[b.id].opening_total == Decimal("40.00")
    assert by_member[b.id].issued_total == Decimal("40.00")
    assert by_member[b.id].share_pct == Decimal("40.00")


def test_empty_org_no_crash(org_factory, user_factory, membership_factory):
    org = org_factory()
    _members(org, org_factory, user_factory, membership_factory, 2)  # members, but no equity
    pie = compute_pie(org)
    assert pie.total == Decimal("0")
    assert all(s.share == Decimal("0") and s.share_pct == Decimal("0") for s in pie.slices)


def test_org_with_no_members(org_factory):
    org = org_factory()
    pie = compute_pie(org)
    assert pie.total == Decimal("0")
    assert pie.slices == []
    assert pie.member_count == 0


def test_slices_sorted_by_issued_desc(
    org_factory, user_factory, membership_factory, make_run, make_line
):
    org = org_factory()
    a, b, c = _members(org, org_factory, user_factory, membership_factory, 3)
    run = make_run(org)
    make_line(org, run, a, Decimal("10.00"))
    make_line(org, run, b, Decimal("70.00"))
    make_line(org, run, c, Decimal("20.00"))
    pie = compute_pie(org)
    totals = [s.issued_total for s in pie.slices]
    assert totals == sorted(totals, reverse=True)
    assert pie.slices[0].membership_id == b.id


# --------------------------------------------------------------------------- #
# Traceability / provenance.
# --------------------------------------------------------------------------- #
def test_provenance_returns_exact_lines_tasks_balances(
    org_factory, user_factory, membership_factory, make_run, make_line, make_task
):
    org = org_factory()
    (a,) = _members(org, org_factory, user_factory, membership_factory, 1)
    run = make_run(org)
    line = make_line(
        org,
        run,
        a,
        Decimal("25.00"),
        computed=Decimal("20.00"),
        adjustment=Decimal("5.00"),
        reason="under-claimed",
    )
    t1 = make_task(org, "PROJ-1", subject="Ship it", url="https://tracker.example/PROJ-1")
    t2 = make_task(org, "PROJ-2")
    line.tasks.set([t1, t2])
    OpeningBalance.objects.create(org=org, membership=a, value=Decimal("5.00"), source_note="seed")

    pie = compute_pie(org)
    s = pie.slices[0]
    assert s.issued_total == Decimal("30.00")

    assert len(s.lines) == 1
    prov = s.lines[0]
    assert prov.line_id == line.id
    assert prov.final_value == Decimal("25.00")
    assert prov.adjustment == Decimal("5.00")
    assert prov.adjustment_reason == "under-claimed"
    task_ids = {t.external_id for t in prov.tasks}
    assert task_ids == {"PROJ-1", "PROJ-2"}

    assert len(s.opening_balances) == 1
    assert s.opening_balances[0].value == Decimal("5.00")
    assert s.opening_balances[0].source_note == "seed"


# --------------------------------------------------------------------------- #
# Personal standing.
# --------------------------------------------------------------------------- #
def test_personal_standing_separates_pending_and_issued(
    org_factory, user_factory, membership_factory, make_run, make_line
):
    org = org_factory()
    a, b = _members(org, org_factory, user_factory, membership_factory, 2)
    approved = make_run(org, state=DropRunState.APPROVED)
    open_run = make_run(org, state=DropRunState.OPEN)
    make_line(org, approved, a, Decimal("60.00"))
    make_line(org, open_run, a, Decimal("15.00"))  # pending
    make_line(org, approved, b, Decimal("40.00"))
    OpeningBalance.objects.create(org=org, membership=a, value=Decimal("0.00"))

    st = compute_personal_standing(org, a)
    assert st.issued_total == Decimal("60.00")
    assert st.realized_total == Decimal("60.00")
    assert st.pending_total == Decimal("15.00")
    # Share is issued/total = 60 / 100.
    assert st.share_pct == Decimal("60.00")
    assert len(st.issued_lines) == 1
    assert len(st.pending_lines) == 1
    assert st.issued_lines[0].final_value == Decimal("60.00")
    assert st.pending_lines[0].final_value == Decimal("15.00")


def test_standing_member_with_nothing(org_factory, user_factory, membership_factory):
    org = org_factory()
    (a,) = _members(org, org_factory, user_factory, membership_factory, 1)
    st = compute_personal_standing(org, a)
    assert st.realized_total == Decimal("0")
    assert st.pending_total == Decimal("0")
    assert st.share_pct == Decimal("0")


# --------------------------------------------------------------------------- #
# Tenant isolation — one org's lines never leak into another's pie.
# --------------------------------------------------------------------------- #
def test_pie_is_org_scoped(org_factory, user_factory, membership_factory, make_run, make_line):
    org1 = org_factory(slug="one")
    org2 = org_factory(slug="two")
    (a,) = _members(org1, org_factory, user_factory, membership_factory, 1)
    (b,) = _members(org2, org_factory, user_factory, membership_factory, 1)
    make_line(org1, make_run(org1), a, Decimal("10.00"))
    make_line(org2, make_run(org2), b, Decimal("999.00"))

    pie = compute_pie(org1)
    assert pie.total == Decimal("10.00")
    assert len(pie.slices) == 1


# --------------------------------------------------------------------------- #
# Pages + API.
# --------------------------------------------------------------------------- #
def test_pie_page_renders_and_drills_down(
    client, org_factory, user_factory, membership_factory, make_run, make_line, make_task
):
    org = _org_with_unit(org_factory, "COOK")
    user = user_factory(email="viewer@example.com")
    membership_factory(org, user, role=MembershipRole.MEMBER)
    run = make_run(org)
    line = make_line(org, run, org.memberships.first(), Decimal("100.00"))
    line.tasks.set([make_task(org, "PROJ-9", subject="Big task")])

    client.force_login(user)
    resp = client.get(f"/o/{org.slug}/pie/")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "COOK" in body
    assert "PROJ-9" in body  # provenance drill-down present in the HTML
    assert "<svg" in body  # inline SVG visual, no external deps


def test_standing_page_renders(
    client, org_factory, user_factory, membership_factory, make_run, make_line
):
    org = org_factory()
    user = user_factory(email="me@example.com")
    m = membership_factory(org, user)
    make_line(org, make_run(org), m, Decimal("42.00"))
    client.force_login(user)
    resp = client.get(f"/o/{org.slug}/pie/me/")
    assert resp.status_code == 200
    assert "42" in resp.content.decode()


def test_api_summary_and_standing(
    client, org_factory, user_factory, membership_factory, make_run, make_line
):
    org = _org_with_unit(org_factory, "COOK")
    user = user_factory(email="api@example.com")
    m = membership_factory(org, user)
    make_line(org, make_run(org), m, Decimal("100.00"))
    client.force_login(user)

    summary = client.get(f"/api/v1/pie/{org.slug}/summary/")
    assert summary.status_code == 200
    data = summary.json()
    assert data["total"] == "100.00"
    assert data["unit_name"] == "COOK"
    assert data["slices"][0]["share_pct"] == "100.00"

    standing = client.get(f"/api/v1/pie/{org.slug}/standing/")
    assert standing.status_code == 200
    sdata = standing.json()
    assert sdata["realized_total"] == "100.00"
    assert sdata["pending_total"] == "0.00"


def test_api_summary_forbidden_for_non_member(client, org_factory, user_factory):
    org = org_factory()
    outsider = user_factory(email="outsider@example.com")
    client.force_login(outsider)
    resp = client.get(f"/api/v1/pie/{org.slug}/summary/")
    assert resp.status_code == 403
