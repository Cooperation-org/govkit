"""
Work-weight core tests: both windows (all_time / trailing_12m incl. the 12-month
boundary), opening balances always counting as all-time, and the zero-issued case.

Reuses the shared factories in tests/conftest.py. Drop lines are created directly in
approved runs (the drop engine has its own tests); here we only exercise the weighting.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.drops.models import DropLine, DropRun, DropRunState
from apps.orgs.models import OpeningBalance, WeightWindow
from apps.orgs.weighting import work_weight, work_weight_map


def _approved_run(org, when=None):
    run = DropRun.objects.create(org=org, state=DropRunState.APPROVED)
    # approved_at is not auto; set it explicitly (optionally backdated for window tests).
    run.approved_at = when or timezone.now()
    run.save(update_fields=["approved_at"])
    return run


def _line(org, run, membership, value):
    line = DropLine(
        org=org,
        run=run,
        membership=membership,
        computed_value=Decimal(value),
        final_value=Decimal(value),
    )
    line.save()
    return line


@pytest.fixture
def setup(org_factory, user_factory, membership_factory):
    def make():
        org = org_factory()
        m = membership_factory(org, user_factory())
        return org, m

    return make


def test_zero_issued_is_zero(setup):
    org, m = setup()
    assert work_weight(org, m, WeightWindow.ALL_TIME) == Decimal("0")
    assert work_weight_map(org, WeightWindow.ALL_TIME) == {m.id: Decimal("0")}


def test_all_time_sums_approved_lines(setup):
    org, m = setup()
    _line(org, _approved_run(org), m, "100.00")
    _line(org, _approved_run(org), m, "50.50")
    assert work_weight(org, m, WeightWindow.ALL_TIME) == Decimal("150.50")


def test_open_runs_do_not_count(setup):
    org, m = setup()
    open_run = DropRun.objects.create(org=org, state=DropRunState.OPEN)
    _line(org, open_run, m, "999.00")
    assert work_weight(org, m, WeightWindow.ALL_TIME) == Decimal("0")


def test_opening_balances_count_as_all_time(setup):
    org, m = setup()
    OpeningBalance.objects.create(org=org, membership=m, value=Decimal("40.00"))
    # Even a stale approved run + opening: opening always counts under both windows.
    _line(org, _approved_run(org, timezone.now() - timedelta(days=400)), m, "10.00")
    assert work_weight(org, m, WeightWindow.ALL_TIME) == Decimal("50.00")
    # Trailing excludes the 400-day-old line but keeps the opening balance.
    assert work_weight(org, m, WeightWindow.TRAILING_12M) == Decimal("40.00")


def test_trailing_12m_excludes_old_and_includes_recent(setup):
    org, m = setup()
    now = timezone.now()
    _line(org, _approved_run(org, now - timedelta(days=30)), m, "20.00")  # recent
    _line(org, _approved_run(org, now - timedelta(days=400)), m, "70.00")  # old
    assert work_weight(org, m, WeightWindow.ALL_TIME) == Decimal("90.00")
    assert work_weight(org, m, WeightWindow.TRAILING_12M) == Decimal("20.00")


def test_trailing_12m_boundary(setup):
    org, m = setup()
    now = timezone.now()
    # Just inside the 365-day window counts; just outside does not.
    _line(org, _approved_run(org, now - timedelta(days=364)), m, "5.00")
    _line(org, _approved_run(org, now - timedelta(days=366)), m, "8.00")
    assert work_weight(org, m, WeightWindow.TRAILING_12M) == Decimal("5.00")


def test_map_covers_every_member(org_factory, user_factory, membership_factory):
    org = org_factory()
    m1 = membership_factory(org, user_factory())
    m2 = membership_factory(org, user_factory())
    m3 = membership_factory(org, user_factory())  # earns nothing
    _line(org, _approved_run(org), m1, "100.00")
    OpeningBalance.objects.create(org=org, membership=m2, value=Decimal("25.00"))
    weights = work_weight_map(org, WeightWindow.ALL_TIME)
    assert weights == {
        m1.id: Decimal("100.00"),
        m2.id: Decimal("25.00"),
        m3.id: Decimal("0"),
    }


def test_map_is_org_scoped(org_factory, user_factory, membership_factory):
    org_a = org_factory()
    org_b = org_factory()
    ma = membership_factory(org_a, user_factory())
    mb = membership_factory(org_b, user_factory())
    _line(org_a, _approved_run(org_a), ma, "10.00")
    _line(org_b, _approved_run(org_b), mb, "99.00")
    assert work_weight_map(org_a, WeightWindow.ALL_TIME) == {ma.id: Decimal("10.00")}
