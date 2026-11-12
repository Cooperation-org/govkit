"""
The welcome a newly joined member sees (apps.orgs.welcome, <govkit-welcome>):
what they joined, the team's CRM and Board, and whether each is ready — read
live from the tool, "unknown" when GovKit has no way to tell.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from apps.access import services
from apps.access.adapters import ToolError
from apps.access.models import ToolConnection
from apps.orgs.models import Membership

EMBED = {"HTTP_X_GOVKIT_EMBED": "1"}


@pytest.fixture(autouse=True)
def _setup(settings):
    settings.GOVKIT_SECRET_KEY = Fernet.generate_key().decode()
    settings.COHORT_CRM_URL = "https://crm-{org_slug}.example/"
    settings.COHORT_BOARD_URL = "https://board.example/p/{org_slug}/board"
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def newcomer(org_factory, user_factory, membership_factory):
    org = org_factory(slug="rtv", display_name="Raise the Voices")
    user = user_factory(email="vol@example.com")
    return membership_factory(org, user)


def _url(m):
    return reverse("org-welcome", kwargs={"org_slug": m.org.slug})


def _dismiss_url(m):
    return reverse("org-welcome-dismiss", kwargs={"org_slug": m.org.slug})


def _conn(org, kind):
    extra = {"database": "crm-rtv"} if kind == "odoo" else {"default_role": "Back"}
    return ToolConnection.objects.create(
        org=org,
        kind=kind,
        display_name=kind,
        base_url=f"https://{kind}.example",
        admin_login="bot",
        admin_secret="pw",
        **extra,
    )


class _Tool:
    def __init__(self, people=None, fail=False):
        self.people, self.fail, self.reads = people or {}, fail, 0

    def read(self):
        self.reads += 1
        if self.fail:
            raise ToolError("unreachable")
        return [{"slug": "app", "name": "App"}], self.people


def _tools(mapping):
    return patch.object(services, "adapter_for", side_effect=lambda c: mapping[c.pk])


@pytest.mark.django_db
def test_new_member_sees_what_they_joined_and_the_team_tools(client, newcomer):
    client.force_login(newcomer.user)
    d = client.get(_url(newcomer)).json()
    assert d["show"] is True
    assert d["org_name"] == "Raise the Voices"
    assert d["role"] == "Member"
    assert [(t["key"], t["url"]) for t in d["tools"]] == [
        ("crm", "https://crm-rtv.example/"),
        ("board", "https://board.example/p/rtv/board"),
    ]


@pytest.mark.django_db
def test_with_no_tool_connection_readiness_is_unknown_not_ready(client, newcomer):
    """Nothing here can tell; the panel must not claim ready."""
    client.force_login(newcomer.user)
    d = client.get(_url(newcomer)).json()
    assert {t["status"] for t in d["tools"]} == {"unknown"}


@pytest.mark.django_db
def test_readiness_is_read_from_the_tools(client, newcomer):
    crm, board = _conn(newcomer.org, "odoo"), _conn(newcomer.org, "taiga")
    client.force_login(newcomer.user)
    tools = {
        crm.pk: _Tool(people={"vol@example.com": {"name": "Vol", "scopes": [""]}}),
        board.pk: _Tool(people={}),
    }
    with _tools(tools):
        d = client.get(_url(newcomer)).json()
    assert {t["key"]: t["status"] for t in d["tools"]} == {
        "crm": "ready",
        "board": "setting_up",
    }


@pytest.mark.django_db
def test_a_tool_that_cannot_be_read_is_unknown(client, newcomer):
    crm = _conn(newcomer.org, "odoo")
    client.force_login(newcomer.user)
    with _tools({crm.pk: _Tool(fail=True)}):
        d = client.get(_url(newcomer)).json()
    assert {t["key"]: t["status"] for t in d["tools"]}["crm"] == "unknown"


@pytest.mark.django_db
def test_polling_reuses_one_tool_read_for_a_few_seconds(client, newcomer):
    crm = _conn(newcomer.org, "odoo")
    tool = _Tool(people={})
    client.force_login(newcomer.user)
    with _tools({crm.pk: tool}):
        for _ in range(4):
            client.get(_url(newcomer))
    assert tool.reads == 1


@pytest.mark.django_db
def test_unset_tool_url_is_left_out(client, newcomer, settings):
    settings.COHORT_BOARD_URL = ""
    client.force_login(newcomer.user)
    d = client.get(_url(newcomer)).json()
    assert [t["key"] for t in d["tools"]] == ["crm"]


@pytest.mark.django_db
def test_closing_it_keeps_it_closed(client, newcomer):
    client.force_login(newcomer.user)
    assert client.post(_dismiss_url(newcomer), **EMBED).status_code == 200
    newcomer.refresh_from_db()
    assert newcomer.welcome_dismissed_at is not None
    assert client.get(_url(newcomer)).json() == {"show": False}


@pytest.mark.django_db
def test_closing_needs_the_embed_header(client, newcomer):
    client.force_login(newcomer.user)
    assert client.post(_dismiss_url(newcomer)).status_code == 403
    newcomer.refresh_from_db()
    assert newcomer.welcome_dismissed_at is None


@pytest.mark.django_db
def test_long_standing_member_sees_no_welcome(client, newcomer):
    Membership.objects.filter(pk=newcomer.pk).update(created_at=timezone.now() - timedelta(days=30))
    client.force_login(newcomer.user)
    assert client.get(_url(newcomer)).json() == {"show": False}


@pytest.mark.django_db
def test_outsider_cannot_read_a_team_welcome(client, newcomer, user_factory):
    client.force_login(user_factory(email="out@example.com"))
    assert client.get(_url(newcomer)).status_code == 403


@pytest.mark.django_db
def test_superuser_without_membership_is_not_welcomed(client, newcomer, user_factory):
    """A superuser looking in has no membership here: nothing to welcome them to."""
    boss = user_factory(email="boss@example.com", is_superuser=True, is_staff=True)
    client.force_login(boss)
    assert client.get(_url(newcomer)).json() == {"show": False}


@pytest.mark.django_db
def test_govkit_dashboard_mounts_the_welcome(client, newcomer):
    client.force_login(newcomer.user)
    page = client.get(reverse("orgs:dashboard", kwargs={"org_slug": "rtv"})).content.decode()
    assert '<govkit-welcome class="gk-card" hidden data-up="http://testserver"' in page
    assert 'data-org="rtv"' in page


def test_tool_url_settings_must_be_templates():
    """A typo fails at startup, not as a dead link in front of a new member."""
    import os
    import subprocess
    import sys

    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "SECRET_KEY": "x",
        "DEBUG": "true",
        "DATABASE_URL": "sqlite:///:memory:",
        "COHORT_CRM_URL": "https://crm.example/",
    }
    r = subprocess.run(
        [sys.executable, "-c", "import django; django.setup()"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0
    assert "COHORT_CRM_URL must be an https URL template" in r.stderr
