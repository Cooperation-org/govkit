"""The worker's own rail: how a person finds out what a venture did about them.

A hand-raise is answered on the venture's dash, where the person who raised it
cannot see it. Without this read they are told nothing (mail is off on the
cohort install) — so the fact has to reach the rail on their own dash.
"""

import pytest

from apps.orgs.models import MembershipRole

NEWS = "/api/v1/commons/news/mine/"


@pytest.fixture
def venture(org_factory):
    return org_factory(slug="cookin", display_name="What's Cookin'")


@pytest.fixture
def worker(user_factory):
    return user_factory(email="worker@example.com")


def _news(client):
    return client.get(NEWS).json()["items"]


def _raise_hand(client, venture, note=""):
    return client.post(
        f"/api/v1/commons/ventures/{venture.slug}/interest/",
        data={"note": note},
        content_type="application/json",
        HTTP_X_GOVKIT_EMBED="1",
    )


def test_a_hand_nobody_answered_says_it_is_waiting(client, venture, worker):
    client.force_login(worker)
    _raise_hand(client, venture, "I can build the pipeline.")
    (row,) = _news(client)
    assert row["kind"] == "interest_waiting"
    assert row["title"] == "Waiting on What's Cookin'"
    assert row["detail"] == "I can build the pipeline."
    assert row["done"] is True


def test_the_venture_answering_shows_up_on_the_workers_rail(
    client, venture, worker, user_factory, membership_factory
):
    client.force_login(worker)
    _raise_hand(client, venture)
    interest_id = venture.interests.get().id

    admin = user_factory(email="admin@example.com")
    membership_factory(org=venture, user=admin, role=MembershipRole.ADMIN)
    client.force_login(admin)
    resp = client.post(
        f"/api/v1/commons/orgs/{venture.slug}/interest/{interest_id}/respond/",
        HTTP_X_GOVKIT_EMBED="1",
    )
    assert resp.status_code == 200

    client.force_login(worker)
    (row,) = _news(client)
    assert row["kind"] == "interest_answered"
    assert row["title"] == "What's Cookin' answered you"
    assert row["done"] is False


def test_being_let_in_reads_as_one_line_with_the_way_in(
    client, settings, venture, worker, user_factory, membership_factory
):
    settings.COHORT_FRONT_DOOR = "https://workers.vc/dash/{org_slug}/"
    client.force_login(worker)
    _raise_hand(client, venture)
    interest_id = venture.interests.get().id

    admin = user_factory(email="admin@example.com")
    membership_factory(org=venture, user=admin, role=MembershipRole.ADMIN)
    client.force_login(admin)
    resp = client.post(
        f"/api/v1/commons/orgs/{venture.slug}/interest/{interest_id}/accept/",
        HTTP_X_GOVKIT_EMBED="1",
    )
    assert resp.status_code == 200

    client.force_login(worker)
    # One event, one line: the membership says it, so the answered interest
    # row it came from is dropped.
    (row,) = _news(client)
    assert row["kind"] == "you_joined"
    assert row["title"] == "You are in — What's Cookin'"
    assert row["url"] == "https://workers.vc/dash/cookin/"
    assert row["done"] is False


def test_answers_come_before_hands_still_waiting(
    client, venture, worker, org_factory, user_factory, membership_factory
):
    other = org_factory(slug="alonovo", display_name="Alonovo")
    client.force_login(worker)
    _raise_hand(client, venture)
    _raise_hand(client, other)
    interest_id = other.interests.get().id

    admin = user_factory(email="admin@example.com")
    membership_factory(org=other, user=admin, role=MembershipRole.ADMIN)
    client.force_login(admin)
    client.post(
        f"/api/v1/commons/orgs/{other.slug}/interest/{interest_id}/respond/",
        HTTP_X_GOVKIT_EMBED="1",
    )

    client.force_login(worker)
    kinds = [r["kind"] for r in _news(client)]
    assert kinds == ["interest_answered", "interest_waiting"]


def test_the_rail_is_mine_only(client, venture, worker, user_factory):
    client.force_login(worker)
    _raise_hand(client, venture)
    client.force_login(user_factory(email="stranger@example.com"))
    assert _news(client) == []


def test_signed_out_gets_nothing(client):
    assert client.get(NEWS).status_code in (401, 403)
