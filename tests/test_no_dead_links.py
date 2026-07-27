"""Walk the org pages a member actually reaches and follow every link on them.

Golda, after clicking one: "get rid of 'my standing' that is bad dead bad broken
link ... check all links ... no 404". That one was real: the page 404s for anyone
without a membership, and it was linked unconditionally.

So this crawls instead of trusting review. Every same-site link on every org page
is fetched as the signed-in member and must not 404 or 500. Add a page to PAGES
when you add one; a link that only works for some people has to be hidden from
everyone else, not left to be found.
"""

import re

import pytest
from django.urls import reverse

from apps.orgs.genesis import start_genesis
from apps.orgs.models import MembershipRole

# Every org-scoped page an admin sees in the tab bar, plus the org home.
PAGES = [
    "orgs:dashboard",
    "orgs:settings",
    "orgs:members",
    "drops:index",
    "pie:index",
    "votes:index",
    "sortition:index",
    "exports:index",
    "tasksources:index",
]

HREF = re.compile(r'href="([^"#][^"]*)"')
# Links off our own site are not ours to keep alive.
EXTERNAL = re.compile(r"^(https?:|mailto:|tel:|//)")


@pytest.fixture
def team(org_factory, user_factory, membership_factory, client):
    org = org_factory(slug="acme", display_name="Acme")
    admin = user_factory()
    membership_factory(org, admin, role=MembershipRole.ADMIN)
    start_genesis(org)
    client.force_login(admin)
    return org


def _links(body):
    return [h for h in HREF.findall(body) if not EXTERNAL.match(h)]


@pytest.mark.django_db
@pytest.mark.parametrize("page", PAGES)
def test_no_page_links_anywhere_broken(page, team, client):
    resp = client.get(reverse(page, kwargs={"org_slug": team.slug}))
    assert resp.status_code == 200, f"{page} itself is broken"

    checked = 0
    for href in _links(resp.content.decode()):
        if href.startswith("/static/"):
            continue
        followed = client.get(href)
        assert followed.status_code not in (
            404,
            500,
        ), f"{page} links to {href} -> {followed.status_code}"
        checked += 1
    assert checked, f"{page} rendered no links at all, which means it rendered wrong"


@pytest.mark.django_db
def test_my_standing_is_not_offered_to_someone_who_has_none(org_factory, user_factory, client):
    """A superuser inspecting a team they are not in has no standing to show.

    The link used to be rendered for them anyway and 404'd. Nothing on the page
    may point at a page that cannot work for the person looking at it.
    """
    org = org_factory(slug="acme", display_name="Acme")
    root = user_factory(is_superuser=True, is_staff=True)
    client.force_login(root)

    body = client.get(reverse("pie:index", kwargs={"org_slug": org.slug})).content.decode()

    assert "My standing" not in body
    assert client.get(reverse("pie:standing", kwargs={"org_slug": org.slug})).status_code == 404
