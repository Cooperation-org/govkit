"""Model-creation + invariant tests across the full schema."""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.drops.models import DropLine, DropRun, DropRunState
from apps.orgs.models import Membership, OpeningBalance
from apps.tasksources.models import TaskSourceConfig, TrackedTask
from apps.votes.models import Ballot, Vote


def test_org_and_config_created(org_factory):
    org = org_factory(slug="acme", display_name="Acme")
    assert org.valuation_config is not None
    assert org.unit_name == "points"


def test_membership_unique_per_org_user(org_factory, user_factory):
    org = org_factory()
    user = user_factory()
    Membership.objects.create(org=org, user=user)
    with pytest.raises(IntegrityError):
        Membership.objects.create(org=org, user=user)


def test_effective_rate_falls_back_to_org_default(org_factory, user_factory):
    org = org_factory()
    org.default_hourly_rate = Decimal("50.00")
    org.save()
    m = Membership.objects.create(org=org, user=user_factory())
    assert m.effective_rate == Decimal("50.00")
    m.hourly_rate = Decimal("80.00")
    assert m.effective_rate == Decimal("80.00")


def test_tracked_task_unique_and_missing_value(org_factory, user_factory):
    org = org_factory()
    source = TaskSourceConfig.objects.create(org=org, base_url="https://tracker.example")
    t = TrackedTask.objects.create(org=org, source=source, external_id="T-1")
    assert t.is_missing_value is True
    t.hours = Decimal("2")
    t.save()
    assert t.is_missing_value is False
    with pytest.raises(IntegrityError):
        TrackedTask.objects.create(org=org, source=source, external_id="T-1")


def test_opening_balance(org_factory, user_factory, membership_factory):
    org = org_factory()
    m = membership_factory(org, user_factory())
    ob = OpeningBalance.objects.create(org=org, membership=m, value=Decimal("100"))
    assert ob.value == Decimal("100")


def test_dropline_requires_reason_for_adjustment(org_factory, user_factory, membership_factory):
    org = org_factory()
    m = membership_factory(org, user_factory())
    run = DropRun.objects.create(org=org)
    line = DropLine(org=org, run=run, membership=m, adjustment=Decimal("5"))
    with pytest.raises(ValidationError):
        line.save()  # save() calls full_clean -> clean()


def test_dropline_immutable_after_approval(org_factory, user_factory, membership_factory):
    from django.utils import timezone

    org = org_factory()
    m = membership_factory(org, user_factory())
    run = DropRun.objects.create(org=org)
    line = DropLine.objects.create(org=org, run=run, membership=m, final_value=Decimal("10"))
    run.state = DropRunState.APPROVED
    run.approved_at = timezone.now()
    run.save()
    line.final_value = Decimal("20")
    with pytest.raises(ValidationError):
        line.save()


def test_ballot_unique_per_vote_member(org_factory, user_factory, membership_factory):
    org = org_factory()
    m = membership_factory(org, user_factory())
    vote = Vote.objects.create(org=org, question="Ship it?", options=["Yes", "No"])
    Ballot.objects.create(org=org, vote=vote, membership=m, choice="Yes")
    with pytest.raises(IntegrityError):
        Ballot.objects.create(org=org, vote=vote, membership=m, choice="No")
