"""
Amebo team-registry reporting on invite acceptance.

Contract (plan 2026-07-16-workersvc-doorway-and-amebo-team-registry.md):
POST {AMEBO_BASE_URL}/api/orgs/provision with Bearer {AMEBO_S2S_TOKEN} — amebo is the
operational system-of-record for teams; GovKit reports each accepted membership so the
person gets provisioned across the team's tools.

HTTP is mocked at the true boundary (``urllib.request.urlopen``), like the Taiga adapter
tests. The reporting runs via ``transaction.on_commit`` (L7: no network I/O inside a DB
transaction), so tests drive the accept flow inside
``django_capture_on_commit_callbacks(execute=True)``.
"""

import json
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.orgs import amebo
from apps.orgs.models import (
    Invite,
    InviteAudience,
    InviteStatus,
    Membership,
    MembershipRole,
    Org,
)

AMEBO_URL = "https://amebo.example.com"
AMEBO_TOKEN = "test-amebo-s2s-secret"


# --- HTTP mock ------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, body):
        self._body = json.dumps(body).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@contextmanager
def mock_amebo(fail_with=None):
    """Patch urlopen to record provision calls (or raise `fail_with`). Yields the calls:
    a list of (url, headers, parsed_json_body) tuples in request order."""
    calls = []

    def fake_urlopen(request, *args, **kwargs):
        if fail_with is not None:
            raise fail_with
        calls.append(
            (
                request.full_url,
                dict(request.header_items()),
                json.loads(request.data.decode("utf-8")),
            )
        )
        return _FakeResponse({"org_id": 1, "created": True, "members": []})

    with patch.object(amebo.urllib.request, "urlopen", side_effect=fake_urlopen):
        yield calls


# --- Fixtures -------------------------------------------------------------------------


@pytest.fixture
def amebo_settings(settings):
    settings.AMEBO_BASE_URL = AMEBO_URL
    settings.AMEBO_S2S_TOKEN = AMEBO_TOKEN
    return settings


@pytest.fixture
def org(org_factory):
    return org_factory(slug="accel", display_name="Earned Gov Accelerator")


@pytest.fixture
def invite(org):
    return Invite.objects.create(org=org, role=MembershipRole.MEMBER, audience="mentor")


@pytest.fixture
def lt_user(user_factory):
    """A user who signed in via LinkedTrust SSO (explicit OIDC identity map)."""
    return user_factory(
        email="jane@example.com",
        display_name="Jane Doe",
        auth_provider="linkedtrust",
        auth_provider_id="lt-sub-123",
    )


def _accept(client, invite, user, django_capture_on_commit_callbacks):
    client.force_login(user)
    url = reverse("orgs:accept_invite", kwargs={"code": invite.code})
    with django_capture_on_commit_callbacks(execute=True):
        resp = client.get(url)
    return resp


# --- Reporting on accept ---------------------------------------------------------------


@pytest.mark.django_db
def test_accept_reports_membership_to_amebo(
    client, invite, lt_user, amebo_settings, django_capture_on_commit_callbacks
):
    with mock_amebo() as calls:
        resp = _accept(client, invite, lt_user, django_capture_on_commit_callbacks)

    assert resp.status_code == 302
    assert Membership.objects.filter(org=invite.org, user=lt_user).exists()

    assert len(calls) == 1
    url, headers, body = calls[0]
    assert url == f"{AMEBO_URL}/api/orgs/provision"
    assert headers.get("Authorization") == f"Bearer {AMEBO_TOKEN}"
    assert headers.get("Content-type") == "application/json"
    assert body == {
        "slug": "accel",
        "name": "Earned Gov Accelerator",
        "source": "govkit-accept",
        "members": [
            {
                "email": "jane@example.com",
                "lt_sub": "lt-sub-123",
                "display_name": "Jane Doe",
                "role": "member",
                "tool_accounts": [
                    {
                        "tool_key": "govkit",
                        "external_id": str(lt_user.pk),
                        "username": None,
                    }
                ],
            }
        ],
    }


@pytest.mark.django_db
def test_accept_reports_admin_role_and_null_lt_sub_for_non_lt_user(
    client, org, user_factory, amebo_settings, django_capture_on_commit_callbacks
):
    """The reported role is the one the membership actually got; lt_sub is null unless
    the user's identity provider is LinkedTrust (identity is explicit, never inferred)."""
    invite = Invite.objects.create(org=org, role=MembershipRole.ADMIN, audience="partner")
    user = user_factory(email="pat@example.com")  # dev/password user: no OIDC identity

    with mock_amebo() as calls:
        _accept(client, invite, user, django_capture_on_commit_callbacks)

    _, _, body = calls[0]
    member = body["members"][0]
    assert member["role"] == "admin"
    assert member["lt_sub"] is None
    assert member["email"] == "pat@example.com"


@pytest.mark.django_db
def test_founder_accept_reports_venture_org_as_admin(
    client, org, lt_user, amebo_settings, django_capture_on_commit_callbacks
):
    invite = Invite.objects.create(
        org=org,
        role=MembershipRole.MEMBER,
        audience=InviteAudience.FOUNDER,
        venture_name="Solar Co-op",
    )

    with mock_amebo() as calls:
        resp = _accept(client, invite, lt_user, django_capture_on_commit_callbacks)

    venture = Org.objects.get(slug="solar-co-op")
    assert resp["Location"] == reverse("orgs:dashboard", kwargs={"org_slug": venture.slug})

    assert len(calls) == 2
    (_, _, first), (_, _, second) = calls
    # First: the invite's org with the role the membership got.
    assert first["slug"] == "accel"
    assert first["members"][0]["role"] == "member"
    # Second: the auto-created venture org, founder as admin.
    assert second["slug"] == "solar-co-op"
    assert second["name"] == "Solar Co-op"
    assert second["members"][0]["role"] == "admin"
    assert second["members"][0]["lt_sub"] == "lt-sub-123"


# --- No-op / failure isolation ----------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("unset", ["AMEBO_BASE_URL", "AMEBO_S2S_TOKEN", "both"])
def test_noop_when_amebo_env_unset(
    client, invite, lt_user, settings, unset, django_capture_on_commit_callbacks
):
    settings.AMEBO_BASE_URL = "" if unset in ("AMEBO_BASE_URL", "both") else AMEBO_URL
    settings.AMEBO_S2S_TOKEN = "" if unset in ("AMEBO_S2S_TOKEN", "both") else AMEBO_TOKEN

    with mock_amebo() as calls:
        resp = _accept(client, invite, lt_user, django_capture_on_commit_callbacks)

    assert resp.status_code == 302
    assert Membership.objects.filter(org=invite.org, user=lt_user).exists()
    assert calls == []  # never touched the network


@pytest.mark.django_db
def test_amebo_failure_never_breaks_accept(
    client, invite, lt_user, amebo_settings, caplog, django_capture_on_commit_callbacks
):
    with mock_amebo(fail_with=OSError("connection refused")):
        resp = _accept(client, invite, lt_user, django_capture_on_commit_callbacks)

    # Acceptance is fully intact: membership created, invite consumed, normal redirect.
    assert resp.status_code == 302
    assert resp["Location"] == reverse("orgs:dashboard", kwargs={"org_slug": invite.org.slug})
    assert Membership.objects.filter(org=invite.org, user=lt_user).exists()
    invite.refresh_from_db()
    assert invite.status == InviteStatus.ACCEPTED
    assert any(
        r.levelname == "WARNING" and "amebo provisioning failed" in r.message
        for r in caplog.records
    )
