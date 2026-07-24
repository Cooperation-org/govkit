"""Org settings page: profile (name, site, socials) + repos, and the context_repo amebo reads.

The form/property parsing is pure Python (no DB). The view + serializer tests exercise
admin gating and that the saved main repo surfaces as `context_repo` for amebo.
"""

import pytest
from django.urls import reverse

from apps.orgs.forms import OrgSettingsForm
from apps.orgs.models import MembershipRole, Org


# --- Parsing + context_repo (no DB) ------------------------------------------------------

def test_form_parses_socials_and_flags_one_main_repo():
    form = OrgSettingsForm(data={
        "display_name": "Acme Coop",
        "website": "acme.coop",
        "socials": "Twitter https://twitter.com/acme\n\nhttps://github.com/acme",
        "main_repo": "github.com/acme/context",
        "other_repos": "github.com/acme/site\ngithub.com/acme/context",
    })
    assert form.is_valid(), form.errors
    assert form.cleaned_data["website"] == "https://acme.coop"
    socials = form.socials_list()
    assert socials[0] == {"label": "Twitter", "url": "https://twitter.com/acme"}
    repos = form.repos_list()
    # Exactly one main, and the main is not duplicated among the others.
    assert [r for r in repos if r["is_main"]] == [{"url": "https://github.com/acme/context", "is_main": True}]
    assert [r["url"] for r in repos if not r["is_main"]] == ["https://github.com/acme/site"]


def test_context_repo_prefers_main_then_first_then_empty():
    assert Org(slug="a", display_name="A", repos=[
        {"url": "https://x/one", "is_main": False},
        {"url": "https://x/two", "is_main": True},
    ]).context_repo == "https://x/two"
    assert Org(slug="b", display_name="B", repos=[
        {"url": "https://x/one", "is_main": False},
    ]).context_repo == "https://x/one"
    assert Org(slug="c", display_name="C", repos=[]).context_repo == ""


def test_empty_settings_clear_without_error():
    form = OrgSettingsForm(data={
        "display_name": "X", "website": "", "socials": "", "main_repo": "", "other_repos": "",
    })
    assert form.is_valid(), form.errors
    assert form.socials_list() == []
    assert form.repos_list() == []


# --- View gating + persistence -----------------------------------------------------------

@pytest.fixture
def admin_org(org_factory, user_factory, membership_factory):
    org = org_factory(slug="team")
    admin = user_factory(email="admin@example.com")
    membership_factory(org=org, user=admin, role=MembershipRole.ADMIN)
    return org, admin


def test_admin_saves_profile_and_main_repo(client, admin_org):
    org, admin = admin_org
    client.force_login(admin)
    url = reverse("orgs:settings", kwargs={"org_slug": org.slug})
    resp = client.post(url, {
        "display_name": "Team Renamed",
        "website": "team.example",
        "socials": "GitHub github.com/team",
        "main_repo": "github.com/team/context",
        "other_repos": "github.com/team/docs",
    })
    assert resp.status_code == 302
    org.refresh_from_db()
    assert org.display_name == "Team Renamed"
    assert org.website == "https://team.example"
    assert org.socials == [{"label": "GitHub", "url": "https://github.com/team"}]
    assert org.context_repo == "https://github.com/team/context"


def test_non_admin_cannot_open_settings(client, admin_org, user_factory, membership_factory):
    org, _ = admin_org
    member = user_factory(email="member@example.com")
    membership_factory(org=org, user=member, role=MembershipRole.MEMBER)
    client.force_login(member)
    resp = client.get(reverse("orgs:settings", kwargs={"org_slug": org.slug}))
    assert resp.status_code == 403


def test_context_repo_is_exposed_in_org_api(client, admin_org):
    org, admin = admin_org
    org.repos = [{"url": "https://github.com/team/context", "is_main": True}]
    org.website = "https://team.example"
    org.save(update_fields=["repos", "website"])
    client.force_login(admin)
    resp = client.get(f"/api/v1/orgs/orgs/{org.slug}/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["context_repo"] == "https://github.com/team/context"
    assert body["website"] == "https://team.example"
