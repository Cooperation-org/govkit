"""Sponsorship offered from the workers.vc page: it lands here, and a human sees it.

The whole point of the table is that nobody who offers money is lost. So the
tests follow that journey: the doorway posts a pledge (S2S, no session), the
org's admins find it on their rail, and marking it answered takes it off.
"""

import json

import pytest
from django.core import mail

from apps.commons.models import SponsorPledge
from apps.orgs.models import MembershipRole

# The embed's preflight gate stands in for a CSRF token cross-origin.
EMBED = {"HTTP_X_GOVKIT_EMBED": "1"}
TOKEN = "s2s-test-token"
ACCEL = "accel"


@pytest.fixture
def accel_org(org_factory, settings):
    settings.ACCELERATOR_ORG_SLUG = ACCEL
    settings.DOORWAY_API_URL = ""  # no doorway in tests
    settings.GOVKIT_S2S_TOKEN = TOKEN
    return org_factory(slug=ACCEL, display_name="Workers VC")


def _post(client, org_slug, payload, token=TOKEN):
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    return client.post(
        f"/api/v1/commons/orgs/{org_slug}/sponsor-pledges/",
        data=json.dumps(payload),
        content_type="application/json",
        **headers,
    )


def _rail(client, org):
    return client.get(f"/api/v1/commons/orgs/{org.slug}/attention/").json()["items"]


def _sign_in(client, org, user_factory, membership_factory, role=MembershipRole.ADMIN):
    user = user_factory(email=f"{role}@example.com")
    membership_factory(org=org, user=user, role=role)
    client.force_login(user)
    return user


class TestIntake:
    def test_a_cash_pledge_is_recorded(self, client, accel_org):
        resp = _post(
            client,
            ACCEL,
            {
                "name": "Dana Sponsor",
                "email": "dana@example.com",
                "org_name": "Dana Capital",
                "tier": "silver",
                "amount": "500",
                "note": "Happy to help with the stipends.",
            },
        )

        assert resp.status_code == 201
        p = SponsorPledge.objects.get(pk=resp.json()["id"])
        assert (p.name, p.org_name, p.tier) == ("Dana Sponsor", "Dana Capital", "silver")
        assert str(p.amount) == "500.00"
        assert p.summary == "$500"
        assert p.responded_at is None

    def test_an_in_kind_offer_needs_no_amount(self, client, accel_org):
        resp = _post(
            client,
            ACCEL,
            {
                "name": "Sam Giver",
                "email": "sam@example.com",
                "kind": "in_kind",
                "offer": "Six months of hosting.",
            },
        )

        assert resp.status_code == 201
        p = SponsorPledge.objects.get(pk=resp.json()["id"])
        assert p.amount is None
        assert p.summary == "in kind"
        assert p.offer == "Six months of hosting."

    def test_a_dollar_sign_and_commas_are_not_a_rejection(self, client, accel_org):
        resp = _post(
            client, ACCEL, {"name": "Ren", "email": "ren@example.com", "amount": "$1,000"}
        )

        assert resp.status_code == 201
        assert str(SponsorPledge.objects.get(pk=resp.json()["id"]).amount) == "1000.00"

    def test_listing_consent_is_asked_not_assumed(self, client, accel_org):
        _post(
            client,
            ACCEL,
            {
                "name": "Quiet Backer",
                "email": "quiet@example.com",
                "list_publicly": False,
                "amount": "200",
            },
        )
        _post(
            client,
            ACCEL,
            {
                "name": "Loud Backer",
                "email": "loud@example.com",
                "listed_as": "The Loud Fund",
                "amount": "200",
            },
        )

        quiet = SponsorPledge.objects.get(name="Quiet Backer")
        loud = SponsorPledge.objects.get(name="Loud Backer")
        assert quiet.public_name == ""
        assert loud.public_name == "The Loud Fund"

    @pytest.mark.parametrize(
        "payload,error",
        [
            ({"email": "a@example.com"}, "name_and_email_required"),
            ({"name": "No Mail"}, "name_and_email_required"),
            ({"name": "Bad Mail", "email": "not-an-email"}, "bad_email"),
            ({"name": "Odd", "email": "a@example.com", "amount": "lots"}, "bad_amount"),
            ({"name": "Zero", "email": "a@example.com", "amount": "0"}, "bad_amount"),
        ],
    )
    def test_bad_input_is_refused(self, client, accel_org, payload, error):
        resp = _post(client, ACCEL, payload)
        assert resp.status_code == 400
        assert resp.json()["error"] == error
        assert not SponsorPledge.objects.exists()

    def test_without_the_shared_secret_nothing_is_written(self, client, accel_org):
        assert _post(client, ACCEL, {"name": "X", "email": "x@example.com"}, token="wrong").status_code == 401
        assert _post(client, ACCEL, {"name": "X", "email": "x@example.com"}, token=None).status_code == 401
        assert not SponsorPledge.objects.exists()

    def test_an_unknown_org_is_a_404_not_a_stray_row(self, client, accel_org):
        assert _post(client, "nobody", {"name": "X", "email": "x@example.com"}).status_code == 404
        assert not SponsorPledge.objects.exists()

    def test_the_sponsor_and_the_team_both_get_told(
        self, client, accel_org, user_factory, membership_factory, settings
    ):
        settings.EMAIL_HOST = "localhost"
        settings.DEFAULT_FROM_EMAIL = "cohort@example.com"
        admin = user_factory(email="admin@example.com")
        membership_factory(org=accel_org, user=admin, role=MembershipRole.ADMIN)

        _post(client, ACCEL, {"name": "Dana", "email": "dana@example.com", "amount": "500"})

        recipients = sorted(sum((m.to for m in mail.outbox), []))
        assert recipients == ["admin@example.com", "dana@example.com"]


class TestTheRail:
    @pytest.fixture
    def pledge(self, client, accel_org):
        _post(
            client,
            ACCEL,
            {"name": "Dana", "email": "dana@example.com", "amount": "500", "note": "for stipends"},
        )
        return SponsorPledge.objects.get()

    def test_an_admin_sees_it_and_can_answer_it(
        self, client, accel_org, pledge, user_factory, membership_factory
    ):
        _sign_in(client, accel_org, user_factory, membership_factory)

        item = next(i for i in _rail(client, accel_org) if i["kind"] == "sponsor_pledge")
        assert "$500" in item["title"] and "Dana" in item["title"]
        assert item["email"] == "dana@example.com"
        assert item["done"] is False

        assert client.post(item["respond_url"], **EMBED).status_code == 200
        pledge.refresh_from_db()
        assert pledge.responded_at is not None

        answered = next(i for i in _rail(client, accel_org) if i["kind"] == "sponsor_pledge")
        assert answered["done"] is True

    def test_an_ordinary_member_does_not_see_who_offered_money(
        self, client, accel_org, pledge, user_factory, membership_factory
    ):
        _sign_in(client, accel_org, user_factory, membership_factory, role=MembershipRole.MEMBER)

        assert [i for i in _rail(client, accel_org) if i["kind"] == "sponsor_pledge"] == []

    def test_a_member_cannot_mark_it_answered(
        self, client, accel_org, pledge, user_factory, membership_factory
    ):
        _sign_in(client, accel_org, user_factory, membership_factory, role=MembershipRole.MEMBER)

        assert client.post(f"/api/v1/commons/sponsor-pledges/{pledge.id}/respond/", **EMBED).status_code == 403
        pledge.refresh_from_db()
        assert pledge.responded_at is None

    def test_a_stranger_cannot_mark_it_answered(self, client, accel_org, pledge):
        assert client.post(f"/api/v1/commons/sponsor-pledges/{pledge.id}/respond/", **EMBED).status_code in (
            401,
            403,
        )
        pledge.refresh_from_db()
        assert pledge.responded_at is None

    def test_the_first_reply_wins(
        self, client, accel_org, pledge, user_factory, membership_factory
    ):
        _sign_in(client, accel_org, user_factory, membership_factory)
        url = f"/api/v1/commons/sponsor-pledges/{pledge.id}/respond/"

        client.post(url, **EMBED)
        pledge.refresh_from_db()
        first = pledge.responded_at

        client.post(url, **EMBED)
        pledge.refresh_from_db()
        assert pledge.responded_at == first
