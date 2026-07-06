"""
Drop-engine tests: valuation math (both modes + multipliers), the steward lifecycle
(review -> adjust-with-reason -> approve -> issued), audit invariants (reason required,
immutable after approval, no double-count across runs) and role gating.

Reuses the shared factories in tests/conftest.py; TrackedTask rows are created directly
(the Taiga adapter is built in parallel and is not a dependency here).
"""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.drops import services
from apps.drops.models import DropRun, DropRunState
from apps.orgs.models import MembershipRole, ValuationMode
from apps.tasksources.models import TaskSourceConfig, TrackedTask

# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def source_factory(db):
    def make(org, **kwargs):
        return TaskSourceConfig.objects.create(
            org=org, base_url=kwargs.pop("base_url", "https://tracker.example"), **kwargs
        )

    return make


@pytest.fixture
def task_factory(db):
    counter = {"n": 0}

    def make(org, source, assignee=None, status="done", **kwargs):
        counter["n"] += 1
        return TrackedTask.objects.create(
            org=org,
            source=source,
            external_id=kwargs.pop("external_id", f"T-{counter['n']}"),
            assignee=assignee,
            status=status,
            **kwargs,
        )

    return make


# ------------------------------------------------------------------- compute_line_value


def _config(org, mode, noncash="1.0", cash="1.0"):
    cfg = org.valuation_config
    cfg.valuation_mode = mode
    cfg.at_risk_multiplier_noncash = Decimal(noncash)
    cfg.at_risk_multiplier_cash = Decimal(cash)
    cfg.save()
    return cfg


def test_hours_rate_basic_with_cash_offset(
    org_factory, user_factory, membership_factory, source_factory, task_factory
):
    org = org_factory()
    org.default_hourly_rate = Decimal("50.00")
    org.save()
    m = membership_factory(org, user_factory())
    src = source_factory(org)
    cfg = _config(org, ValuationMode.HOURS_RATE)
    # 10h * 50 = 500, minus 120 cash = 380 ; plus 2h * 50 = 100
    t1 = task_factory(org, src, assignee=m, hours=Decimal("10"), cash=Decimal("120"))
    t2 = task_factory(org, src, assignee=m, hours=Decimal("2"))
    assert services.compute_line_value(m, [t1, t2], cfg) == Decimal("480.00")


def test_hours_rate_per_member_rate_overrides_default(
    org_factory, user_factory, membership_factory, source_factory, task_factory
):
    org = org_factory()
    org.default_hourly_rate = Decimal("50.00")
    org.save()
    m = membership_factory(org, user_factory(), hourly_rate=Decimal("80.00"))
    src = source_factory(org)
    cfg = _config(org, ValuationMode.HOURS_RATE)
    t = task_factory(org, src, assignee=m, hours=Decimal("3"))
    assert services.compute_line_value(m, [t], cfg) == Decimal("240.00")


def test_hours_rate_at_risk_multipliers(
    org_factory, user_factory, membership_factory, source_factory, task_factory
):
    org = org_factory()
    org.default_hourly_rate = Decimal("50.00")
    org.save()
    m = membership_factory(org, user_factory())
    src = source_factory(org)
    # Slicing-Pie style: 2x non-cash sweat, 4x cash.
    cfg = _config(org, ValuationMode.HOURS_RATE, noncash="2.0", cash="4.0")
    # sweat 10h*50=500 *2 = 1000 ; cash 120 *4 = 480 ; net 520
    t = task_factory(org, src, assignee=m, hours=Decimal("10"), cash=Decimal("120"))
    assert services.compute_line_value(m, [t], cfg) == Decimal("520.00")


def test_direct_value_mode_sums_claimed(
    org_factory, user_factory, membership_factory, source_factory, task_factory
):
    org = org_factory()
    m = membership_factory(org, user_factory())
    src = source_factory(org)
    cfg = _config(org, ValuationMode.DIRECT_VALUE)
    t1 = task_factory(org, src, assignee=m, claimed_value=Decimal("5"))
    t2 = task_factory(org, src, assignee=m, claimed_value=Decimal("8"))
    assert services.compute_line_value(m, [t1, t2], cfg) == Decimal("13.00")


def test_direct_value_mode_with_multiplier(
    org_factory, user_factory, membership_factory, source_factory, task_factory
):
    org = org_factory()
    m = membership_factory(org, user_factory())
    src = source_factory(org)
    cfg = _config(org, ValuationMode.DIRECT_VALUE, noncash="2.0")
    t = task_factory(org, src, assignee=m, claimed_value=Decimal("5"))
    assert services.compute_line_value(m, [t], cfg) == Decimal("10.00")


def test_missing_rate_and_missing_value_contribute_zero(
    org_factory, user_factory, membership_factory, source_factory, task_factory
):
    org = org_factory()  # no default rate
    m = membership_factory(org, user_factory())
    src = source_factory(org)
    cfg = _config(org, ValuationMode.HOURS_RATE)
    t = task_factory(org, src, assignee=m, hours=Decimal("10"))  # no rate -> 0
    assert services.compute_line_value(m, [t], cfg) == Decimal("0.00")


# ----------------------------------------------------------------- open / lifecycle


def test_open_run_groups_by_member_and_computes(
    org_factory, user_factory, membership_factory, source_factory, task_factory
):
    org = org_factory()
    org.default_hourly_rate = Decimal("10.00")
    org.save()
    _config(org, ValuationMode.HOURS_RATE)
    src = source_factory(org)
    m1 = membership_factory(org, user_factory())
    m2 = membership_factory(org, user_factory())
    task_factory(org, src, assignee=m1, hours=Decimal("3"))
    task_factory(org, src, assignee=m1, hours=Decimal("2"))
    task_factory(org, src, assignee=m2, hours=Decimal("4"))

    run = services.open_run(org, opened_by_membership=m1, opened_by_user=m1.user)
    assert run.state == DropRunState.OPEN
    assert run.lines.count() == 2
    line1 = run.lines.get(membership=m1)
    line2 = run.lines.get(membership=m2)
    assert line1.computed_value == Decimal("50.00")  # (3+2)*10
    assert line2.computed_value == Decimal("40.00")  # 4*10
    assert line1.final_value == line1.computed_value


def test_open_run_excludes_unassigned_and_non_done(
    org_factory, source_factory, membership_factory, user_factory, task_factory
):
    org = org_factory()
    org.default_hourly_rate = Decimal("10.00")
    org.save()
    _config(org, ValuationMode.HOURS_RATE)
    src = source_factory(org)
    m = membership_factory(org, user_factory())
    task_factory(org, src, assignee=m, hours=Decimal("1"))  # eligible
    task_factory(org, src, assignee=None, hours=Decimal("9"))  # unassigned -> skip
    task_factory(org, src, assignee=m, status="in-progress", hours=Decimal("9"))  # not done

    run = services.open_run(org, opened_by_membership=m, opened_by_user=m.user)
    assert run.lines.count() == 1
    assert run.lines.first().computed_value == Decimal("10.00")


def test_open_run_raises_when_nothing_eligible(org_factory):
    org = org_factory()
    _config(org, ValuationMode.HOURS_RATE)
    with pytest.raises(services.NoEligibleTasks):
        services.open_run(org)


def test_review_queue_flags_missing_value_tasks(
    org_factory, user_factory, membership_factory, source_factory, task_factory
):
    org = org_factory()
    org.default_hourly_rate = Decimal("10.00")
    org.save()
    _config(org, ValuationMode.HOURS_RATE)
    src = source_factory(org)
    m = membership_factory(org, user_factory())
    task_factory(org, src, assignee=m, hours=Decimal("3"))  # has value
    task_factory(org, src, assignee=m)  # missing value (no hours/claimed)

    run = services.open_run(org, opened_by_membership=m, opened_by_user=m.user)
    payload = services.review_queue(run)
    assert len(payload["missing_value_tasks"]) == 1


def test_adjust_then_approve_lifecycle(
    org_factory, user_factory, membership_factory, source_factory, task_factory
):
    org = org_factory()
    org.default_hourly_rate = Decimal("10.00")
    org.save()
    _config(org, ValuationMode.HOURS_RATE)
    src = source_factory(org)
    m = membership_factory(org, user_factory(), role=MembershipRole.STEWARD)
    task_factory(org, src, assignee=m, hours=Decimal("5"))  # computed 50

    run = services.open_run(org, opened_by_membership=m, opened_by_user=m.user)
    line = run.lines.get()
    services.adjust_line(line, Decimal("15.00"), "corrected under-claim")
    line.refresh_from_db()
    assert line.adjustment == Decimal("15.00")
    assert line.final_value == Decimal("65.00")
    assert line.adjustment_reason == "corrected under-claim"

    services.approve_run(run, approved_by_user=m.user)
    run.refresh_from_db()
    assert run.state == DropRunState.APPROVED
    assert run.approved_at is not None


def test_adjust_without_reason_rejected(
    org_factory, user_factory, membership_factory, source_factory, task_factory
):
    org = org_factory()
    org.default_hourly_rate = Decimal("10.00")
    org.save()
    _config(org, ValuationMode.HOURS_RATE)
    src = source_factory(org)
    m = membership_factory(org, user_factory())
    task_factory(org, src, assignee=m, hours=Decimal("5"))
    run = services.open_run(org, opened_by_membership=m, opened_by_user=m.user)
    line = run.lines.get()
    with pytest.raises(ValidationError):
        services.adjust_line(line, Decimal("15.00"), "")


def test_line_immutable_after_approval(
    org_factory, user_factory, membership_factory, source_factory, task_factory
):
    org = org_factory()
    org.default_hourly_rate = Decimal("10.00")
    org.save()
    _config(org, ValuationMode.HOURS_RATE)
    src = source_factory(org)
    m = membership_factory(org, user_factory())
    task_factory(org, src, assignee=m, hours=Decimal("5"))
    run = services.open_run(org, opened_by_membership=m, opened_by_user=m.user)
    services.approve_run(run)
    line = run.lines.get()
    with pytest.raises(ValidationError):
        services.adjust_line(line, Decimal("1.00"), "too late")


def test_approve_twice_rejected(
    org_factory, user_factory, membership_factory, source_factory, task_factory
):
    org = org_factory()
    org.default_hourly_rate = Decimal("10.00")
    org.save()
    _config(org, ValuationMode.HOURS_RATE)
    src = source_factory(org)
    m = membership_factory(org, user_factory())
    task_factory(org, src, assignee=m, hours=Decimal("5"))
    run = services.open_run(org, opened_by_membership=m, opened_by_user=m.user)
    services.approve_run(run)
    with pytest.raises(ValueError):
        services.approve_run(run)


def test_approved_tasks_excluded_from_next_run(
    org_factory, user_factory, membership_factory, source_factory, task_factory
):
    org = org_factory()
    org.default_hourly_rate = Decimal("10.00")
    org.save()
    _config(org, ValuationMode.HOURS_RATE)
    src = source_factory(org)
    m = membership_factory(org, user_factory())
    task_factory(org, src, assignee=m, hours=Decimal("5"))

    run1 = services.open_run(org, opened_by_membership=m, opened_by_user=m.user)
    services.approve_run(run1)
    # Its task is now linked to an approved run's line -> not gathered again.
    with pytest.raises(services.NoEligibleTasks):
        services.open_run(org)

    # A newly-done task is eligible; the already-dropped one stays excluded (no double count).
    task_factory(org, src, assignee=m, hours=Decimal("2"))
    run2 = services.open_run(org, opened_by_membership=m, opened_by_user=m.user)
    assert run2.lines.get().computed_value == Decimal("20.00")


# --------------------------------------------------------------------- HTTP + role gating


def _login(client, user):
    client.force_login(user)


def test_member_cannot_open_run_via_view(client, org_factory, user_factory, membership_factory):
    org = org_factory()
    member = user_factory()
    membership_factory(org, member, role=MembershipRole.MEMBER)
    _login(client, member)
    resp = client.post(reverse("drops:open_run", kwargs={"org_slug": org.slug}))
    assert resp.status_code == 403


def test_steward_can_open_and_approve_via_views(
    client, org_factory, user_factory, membership_factory, source_factory, task_factory
):
    org = org_factory()
    org.default_hourly_rate = Decimal("10.00")
    org.save()
    _config(org, ValuationMode.HOURS_RATE)
    src = source_factory(org)
    steward = user_factory()
    m = membership_factory(org, steward, role=MembershipRole.STEWARD)
    task_factory(org, src, assignee=m, hours=Decimal("5"))

    _login(client, steward)
    resp = client.post(reverse("drops:open_run", kwargs={"org_slug": org.slug}))
    assert resp.status_code == 302  # -> review
    run = DropRun.objects.for_org(org).get()

    # Adjust via HTMX endpoint returns the row partial.
    line = run.lines.get()
    resp = client.post(
        reverse("drops:adjust_line", kwargs={"org_slug": org.slug, "line_id": line.pk}),
        {"adjustment": "5.00", "adjustment_reason": "bump"},
    )
    assert resp.status_code == 200
    line.refresh_from_db()
    assert line.final_value == Decimal("55.00")

    resp = client.post(
        reverse("drops:approve_run", kwargs={"org_slug": org.slug, "run_id": run.pk})
    )
    assert resp.status_code == 302
    run.refresh_from_db()
    assert run.state == DropRunState.APPROVED


def test_member_cannot_adjust_via_view(
    client, org_factory, user_factory, membership_factory, source_factory, task_factory
):
    org = org_factory()
    org.default_hourly_rate = Decimal("10.00")
    org.save()
    _config(org, ValuationMode.HOURS_RATE)
    src = source_factory(org)
    steward = user_factory()
    m = membership_factory(org, steward, role=MembershipRole.STEWARD)
    task_factory(org, src, assignee=m, hours=Decimal("5"))
    run = services.open_run(org, opened_by_membership=m, opened_by_user=steward)
    line = run.lines.get()

    member = user_factory()
    membership_factory(org, member, role=MembershipRole.MEMBER)
    _login(client, member)
    resp = client.post(
        reverse("drops:adjust_line", kwargs={"org_slug": org.slug, "line_id": line.pk}),
        {"adjustment": "5.00", "adjustment_reason": "x"},
    )
    assert resp.status_code == 403


def test_api_open_run_and_role_gate(
    client, org_factory, user_factory, membership_factory, source_factory, task_factory
):
    org = org_factory()
    org.default_hourly_rate = Decimal("10.00")
    org.save()
    _config(org, ValuationMode.HOURS_RATE)
    src = source_factory(org)
    steward = user_factory()
    m = membership_factory(org, steward, role=MembershipRole.STEWARD)
    task_factory(org, src, assignee=m, hours=Decimal("5"))

    base = f"/api/v1/drops/orgs/{org.slug}/runs/"
    # Member is forbidden from opening.
    member = user_factory()
    membership_factory(org, member, role=MembershipRole.MEMBER)
    _login(client, member)
    assert client.post(base).status_code == 403

    # Steward opens a run.
    _login(client, steward)
    resp = client.post(base)
    assert resp.status_code == 201
    data = resp.json()
    assert data["state"] == "open"
    assert len(data["lines"]) == 1

    run_id = data["id"]
    line_id = data["lines"][0]["id"]
    # Adjust via API.
    resp = client.post(
        f"/api/v1/drops/orgs/{org.slug}/lines/{line_id}/adjust/",
        data={"adjustment": "5.00", "adjustment_reason": "api bump"},
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.json()["final_value"] == "55.00"

    # Approve via API; then adjust is a 409 (immutable).
    resp = client.post(f"/api/v1/drops/orgs/{org.slug}/runs/{run_id}/approve/")
    assert resp.status_code == 200
    resp = client.post(
        f"/api/v1/drops/orgs/{org.slug}/lines/{line_id}/adjust/",
        data={"adjustment": "1.00", "adjustment_reason": "late"},
        content_type="application/json",
    )
    assert resp.status_code == 409
