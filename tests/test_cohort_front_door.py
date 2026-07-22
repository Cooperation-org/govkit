"""
COHORT_FRONT_DOOR: on a cohort deployment the workers.vc dash is THE front door, so
every path that completes an invite join must land the new member on the dash's connect
route ({org_slug} substituted). Unset -> exactly today's behavior (orgs:dashboard).

Startup validation is tested the same way as the SECRET_KEY guard (clean-subprocess
import, see tests/test_settings_secret_key.py) — those tests need no database. The
accept-path tests cover both join completions: the signed-in accept_invite branch
(plain membership and founder venture) and the consume_pending_invite path that
finishes after login.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.urls import reverse

from apps.orgs.models import Invite, InviteAudience, Membership, MembershipRole

FRONT_DOOR = "https://dash.example/dash/{org_slug}/connect/"

# --------------------------------------------------------------- startup validation

REPO_ROOT = Path(__file__).resolve().parent.parent
_SNIPPET = "import config.settings  # noqa: F401\nprint('IMPORT_OK')\n"


def _import_settings(front_door):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(REPO_ROOT),
        "DEBUG": "True",
        "SECRET_KEY": "test-front-door-key",
        "COHORT_FRONT_DOOR": front_door,
    }
    return subprocess.run(
        [sys.executable, "-c", _SNIPPET],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def test_valid_template_starts():
    result = _import_settings("https://workers.vc/dash/{org_slug}/connect/")
    assert result.returncode == 0, result.stderr
    assert "IMPORT_OK" in result.stdout


def test_unset_starts():
    result = _import_settings("")
    assert result.returncode == 0, result.stderr
    assert "IMPORT_OK" in result.stdout


@pytest.mark.parametrize(
    "bad",
    [
        "http://workers.vc/dash/{org_slug}/connect/",  # not https
        "https://workers.vc/dash/connect/",  # missing {org_slug}
        "https://workers.vc/dash/{org_slug}/{oops}/",  # stray placeholder
        "https://workers.vc/dash/{org_slug/connect/",  # unbalanced braces
    ],
)
def test_bad_template_fails_at_startup(bad):
    result = _import_settings(bad)
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "COHORT_FRONT_DOOR" in result.stderr
    assert "IMPORT_OK" not in result.stdout


# --------------------------------------------------------------- accept-path redirects


@pytest.fixture
def org_and_invite(org_factory, user_factory):
    org = org_factory(slug="accel")
    invite = Invite.objects.create(
        org=org, role=MembershipRole.MEMBER, name="Jo", audience="mentor"
    )
    return org, invite


def _accept(client, invite):
    return client.get(reverse("orgs:accept_invite", kwargs={"code": invite.code}))


@pytest.mark.django_db
def test_signed_in_accept_unset_lands_on_govkit_dashboard(client, org_and_invite, user_factory):
    org, invite = org_and_invite
    client.force_login(user_factory())
    resp = _accept(client, invite)
    assert resp.status_code == 302
    assert resp["Location"] == reverse("orgs:dashboard", kwargs={"org_slug": org.slug})


@pytest.mark.django_db
def test_signed_in_accept_lands_on_front_door(client, org_and_invite, user_factory, settings):
    settings.COHORT_FRONT_DOOR = FRONT_DOOR
    org, invite = org_and_invite
    user = user_factory()
    client.force_login(user)
    resp = _accept(client, invite)
    assert resp.status_code == 302
    assert resp["Location"] == "https://dash.example/dash/accel/connect/"
    assert Membership.objects.filter(org=org, user=user).exists()  # the join still happened


@pytest.mark.django_db
def test_founder_accept_lands_on_the_venture_orgs_front_door(
    client, org_and_invite, user_factory, settings
):
    """A founder invite spawns the venture org — the front door gets ITS slug."""
    settings.COHORT_FRONT_DOOR = FRONT_DOOR
    org, _ = org_and_invite
    invite = Invite.objects.create(
        org=org,
        role=MembershipRole.MEMBER,
        audience=InviteAudience.FOUNDER,
        venture_name="Wayfern",
    )
    client.force_login(user_factory())
    resp = _accept(client, invite)
    assert resp.status_code == 302
    assert resp["Location"] == "https://dash.example/dash/wayfern/connect/"


@pytest.mark.django_db
def test_post_login_join_unset_lands_on_govkit_dashboard(
    client, org_and_invite, user_factory, settings
):
    settings.GOVKIT_DEV_LOGIN = True
    org, invite = org_and_invite
    _accept(client, invite)  # anonymous: door renders, code stashed in session
    assert client.session.get("pending_invite_code") == invite.code
    user_factory(email="new@example.com")
    resp = client.post(
        reverse("accounts:dev_login"), {"email": "new@example.com", "password": "pw12345!"}
    )
    assert resp.status_code == 302
    assert resp["Location"] == reverse("orgs:dashboard", kwargs={"org_slug": org.slug})


@pytest.mark.django_db
def test_post_login_join_lands_on_front_door(client, org_and_invite, user_factory, settings):
    settings.GOVKIT_DEV_LOGIN = True
    settings.COHORT_FRONT_DOOR = FRONT_DOOR
    org, invite = org_and_invite
    _accept(client, invite)
    user_factory(email="new@example.com")
    resp = client.post(
        reverse("accounts:dev_login"), {"email": "new@example.com", "password": "pw12345!"}
    )
    assert resp.status_code == 302
    assert resp["Location"] == "https://dash.example/dash/accel/connect/"
    assert Membership.objects.filter(org=org, user__email="new@example.com").exists()


@pytest.mark.django_db
def test_door_created_account_lands_on_front_door(client, org_and_invite, settings):
    """The anonymous one-button door path shares _finish_accept — same landing."""
    settings.COHORT_FRONT_DOOR = FRONT_DOOR
    org, invite = org_and_invite
    url = reverse("orgs:accept_invite", kwargs={"code": invite.code})
    resp = client.post(url, {"email": "fresh@example.com"})
    assert resp.status_code == 302
    assert resp["Location"] == "https://dash.example/dash/accel/connect/"
    assert Membership.objects.filter(org=org, user__email="fresh@example.com").exists()
