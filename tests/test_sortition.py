"""
Sortition tests: determinism (same seed + weights → identical seats; a different seed can
differ), weight-proportionality (heavier members are selected more often over many seeds),
auditability (the stored result JSON reproduces the draw), the seeded-uniform fallback for
zero-weight pools, and role gating on views + API.
"""

from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.drops.models import DropLine, DropRun, DropRunState
from apps.orgs.models import MembershipRole, WeightWindow
from apps.sortition import services
from apps.sortition.models import SortitionDraw


def _approved_line(org, membership, value):
    run = DropRun.objects.create(org=org, state=DropRunState.APPROVED)
    run.approved_at = timezone.now()
    run.save(update_fields=["approved_at"])
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
def weighted_org(org_factory, user_factory, membership_factory):
    """Org with members of weights 100 / 50 / 10 / 1 (ids stable within a test)."""
    org = org_factory()
    members = {}
    for label, value in [("a", "100"), ("b", "50"), ("c", "10"), ("d", "1")]:
        m = membership_factory(org, user_factory())
        _approved_line(org, m, value)
        members[label] = m
    return org, members


# --------------------------------------------------------------- pure algorithm: determinism
def test_same_seed_same_weights_identical():
    weights = {1: Decimal("100"), 2: Decimal("50"), 3: Decimal("10"), 4: Decimal("1")}
    a = services.weighted_sample_without_replacement("seed-x", weights, 2)
    b = services.weighted_sample_without_replacement("seed-x", weights, 2)
    assert a == b
    assert len(a) == len(set(a)) == 2  # distinct, without replacement


def test_different_seed_can_differ():
    weights = {i: Decimal("1") for i in range(1, 21)}  # equal weights, big pool
    seeds = [str(s) for s in range(30)]
    outcomes = {tuple(services.weighted_sample_without_replacement(s, weights, 3)) for s in seeds}
    assert len(outcomes) > 1  # not all seeds produce the same committee


def test_seats_capped_at_pool_size():
    weights = {1: Decimal("5"), 2: Decimal("5")}
    drawn = services.weighted_sample_without_replacement("s", weights, 10)
    assert sorted(drawn) == [1, 2]  # everyone, no duplicates


def test_zero_weight_pool_uses_seeded_uniform_fallback():
    weights = {1: Decimal("0"), 2: Decimal("0"), 3: Decimal("0")}
    a = services.weighted_sample_without_replacement("s", weights, 2)
    b = services.weighted_sample_without_replacement("s", weights, 2)
    assert a == b  # still deterministic
    assert len(set(a)) == 2


def test_higher_weight_selected_more_often():
    weights = {1: Decimal("100"), 2: Decimal("1")}
    heavy_wins = 0
    trials = 400
    for s in range(trials):
        first = services.weighted_sample_without_replacement(f"seed-{s}", weights, 1)[0]
        if first == 1:
            heavy_wins += 1
    # ~100:1 odds; require a strong majority (well below the true ~99% to avoid flakiness).
    assert heavy_wins > trials * 0.85


# ----------------------------------------------------------------- persisted draw + audit
def test_run_draw_persists_snapshot_and_selection(weighted_org):
    org, members = weighted_org
    draw = services.run_draw(org, 2, WeightWindow.ALL_TIME, "meeting-1")
    assert draw.result["seats"] == 2
    assert draw.result["seed"] == "meeting-1"
    assert len(draw.result["selected"]) == 2
    # The full weight snapshot is stored for every member (auditable).
    assert draw.result["weights"][str(members["a"].id)] == "100.00"
    assert draw.result["eligible_count"] == 4


def test_stored_result_reproduces_the_draw(weighted_org):
    org, _ = weighted_org
    draw = services.run_draw(org, 3, WeightWindow.ALL_TIME, "audit-seed")
    # Verification re-runs the STORED seed over the STORED weights.
    assert services.verify_draw(draw) is True
    assert services.reproduce(draw) == draw.result["selected"]


def test_verify_independent_of_later_earnings(weighted_org):
    org, members = weighted_org
    draw = services.run_draw(org, 2, WeightWindow.ALL_TIME, "frozen")
    _approved_line(org, members["d"], "99999")  # d gets rich after the draw
    # Verification uses the stored snapshot, so it still reproduces exactly.
    assert services.verify_draw(draw) is True


def test_run_draw_rejects_bad_input(weighted_org):
    org, _ = weighted_org
    with pytest.raises(services.SortitionError):
        services.run_draw(org, 0, WeightWindow.ALL_TIME, "s")
    with pytest.raises(services.SortitionError):
        services.run_draw(org, 2, WeightWindow.ALL_TIME, "  ")


def test_run_draw_rejects_empty_org(org_factory):
    org = org_factory()
    with pytest.raises(services.SortitionError):
        services.run_draw(org, 1, WeightWindow.ALL_TIME, "s")


def test_selected_seats_resolves_members_with_weight(weighted_org):
    org, _ = weighted_org
    draw = services.run_draw(org, 2, WeightWindow.ALL_TIME, "s")
    seats = services.selected_seats(draw)
    assert [s.seat for s in seats] == [1, 2]
    assert all(s.membership.org_id == org.id for s in seats)
    assert all(s.weight for s in seats)


# ------------------------------------------------------------------- HTTP + role gating
def test_member_cannot_run_draw_via_view(client, org_factory, user_factory, membership_factory):
    org = org_factory()
    member = user_factory()
    membership_factory(org, member, role=MembershipRole.MEMBER)
    client.force_login(member)
    resp = client.post(
        reverse("sortition:run", kwargs={"org_slug": org.slug}),
        {"seats": "2", "window": WeightWindow.ALL_TIME, "seed": "x"},
    )
    assert resp.status_code == 403


def test_steward_run_and_verify_via_views(client, org_factory, user_factory, membership_factory):
    org = org_factory()
    steward = user_factory()
    m = membership_factory(org, steward, role=MembershipRole.STEWARD)
    _approved_line(org, m, "10.00")
    client.force_login(steward)
    resp = client.post(
        reverse("sortition:run", kwargs={"org_slug": org.slug}),
        {"seats": "1", "window": WeightWindow.ALL_TIME, "seed": "meeting"},
    )
    assert resp.status_code == 302
    draw = SortitionDraw.objects.for_org(org).get()
    resp = client.get(
        reverse("sortition:verify", kwargs={"org_slug": org.slug, "draw_id": draw.pk})
    )
    assert resp.status_code == 200
    assert b"Verified" in resp.content


def test_api_run_and_verify_and_role_gate(client, org_factory, user_factory, membership_factory):
    org = org_factory()
    steward = user_factory()
    ms = membership_factory(org, steward, role=MembershipRole.STEWARD)
    _approved_line(org, ms, "10.00")
    member = user_factory()
    membership_factory(org, member, role=MembershipRole.MEMBER)

    base = f"/api/v1/sortition/orgs/{org.slug}/draws/"
    payload = {"seats": 1, "weight_window": WeightWindow.ALL_TIME, "seed": "api-seed"}

    # Member cannot run a draw.
    client.force_login(member)
    assert client.post(base, data=payload, content_type="application/json").status_code == 403

    # Steward runs it.
    client.force_login(steward)
    resp = client.post(base, data=payload, content_type="application/json")
    assert resp.status_code == 201
    draw_id = resp.json()["id"]
    assert resp.json()["verified"] is True

    # Anyone (member) can verify.
    client.force_login(member)
    resp = client.get(f"{base}{draw_id}/verify/")
    assert resp.status_code == 200
    assert resp.json()["verified"] is True
    assert resp.json()["stored_selected"] == resp.json()["reproduced_selected"]
