"""The join page: the asks a team publishes, what the setup screen says is missing,
and the pull that drafts the page from the team's own site.

The pull is the part worth guarding. It takes a URL from a user, so the tests that
matter are the ones proving it will not fetch a private address, and that it never
overwrites something the team already wrote.
"""

from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.orgs import sitepull
from apps.orgs.forms import OrgSettingsForm
from apps.orgs.models import MembershipRole, Org

# --- What the team publishes (no DB) ------------------------------------------------------


def test_asks_keep_order_and_drop_the_ones_with_no_role():
    org = Org(
        slug="a",
        display_name="A",
        looking_for=[
            {"role": "Backend developer", "detail": "our API needs someone"},
            {"role": "  ", "detail": "orphan detail"},
            {"role": "Designer"},
        ],
    )
    assert org.asks == [
        {"role": "Backend developer", "detail": "our API needs someone"},
        {"role": "Designer", "detail": ""},
    ]


def test_form_reads_one_ask_per_line_with_an_optional_detail():
    form = OrgSettingsForm(
        data={
            "display_name": "Acme",
            "looking_for": "Backend developer: shipped a Django app\n\n- Designer\n   \n",
        }
    )
    assert form.is_valid(), form.errors
    assert form.looking_for_list() == [
        {"role": "Backend developer", "detail": "shipped a Django app"},
        {"role": "Designer", "detail": ""},
    ]


def test_the_checklist_is_things_to_do_and_all_tick_when_filled():
    bare = Org(slug="a", display_name="A")
    assert [done for _label, done in bare.profile_checklist()] == [False] * 6
    assert not bare.profile_ready

    done = Org(
        slug="a",
        display_name="A",
        looking_for=[{"role": "Designer", "detail": ""}],
        pitch="What we are building.",
        tagline="A thing",
        cover_image_url="https://acme.test/og.png",
        logo_url="https://acme.test/logo.png",
        website="https://acme.test",
    )
    assert all(d for _label, d in done.profile_checklist())
    assert done.profile_ready


# --- The pull refuses anything that is not a public website -------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1:8000/",
        "http://localhost/",
        "http://10.0.0.100/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
    ],
)
def test_pull_refuses_anything_that_is_not_a_public_website(url):
    with pytest.raises(sitepull.SiteUnreachable):
        sitepull.fetch_profile(url)


def test_pull_refuses_a_redirect_onto_a_private_address():
    """A public host must not be able to bounce us onto the metadata service."""

    class _Response:
        status = 302
        headers = {"Location": "http://169.254.169.254/latest/meta-data/"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, _n):
            return b""

    class _Opener:
        def open(self, *args, **kwargs):
            return _Response()

    with patch.object(sitepull, "_no_redirect_opener", lambda: _Opener()):
        with patch.object(sitepull, "_is_public_address", lambda host: host == "acme.test"):
            with pytest.raises(sitepull.SiteUnreachable):
                sitepull.fetch_profile("https://acme.test")


# --- The pull reads their words, and only their words -------------------------------------

PAGE = """
<html><head>
<title>IntegralMass | Risk Intelligence Platform</title>
<meta name="description" content="Unified risk intelligence platform for site reports.">
<meta property="og:image" content="/og.jpg">
<link rel="icon" href="/logo.png">
</head><body>
<a href="https://www.linkedin.com/company/acme">us</a>
<a href="https://x.com">bare share button</a>
</body></html>
"""


def _pulled(html=PAGE, url="https://acme.test/"):
    with patch.object(sitepull, "_read", lambda _u: (html, url)):
        return sitepull.fetch_profile("acme.test")


def test_pull_splits_the_title_into_a_name_and_a_line():
    drafted = _pulled()
    assert drafted["display_name"] == "IntegralMass"
    assert drafted["tagline"] == "Risk Intelligence Platform"
    assert drafted["pitch"] == "Unified risk intelligence platform for site reports."


def test_pull_makes_image_urls_absolute_and_skips_bare_share_links():
    drafted = _pulled()
    assert drafted["cover_image_url"] == "https://acme.test/og.jpg"
    assert drafted["logo_url"] == "https://acme.test/logo.png"
    # x.com with no path is a share button, not their profile.
    assert drafted["socials"] == [
        {"label": "LinkedIn", "url": "https://www.linkedin.com/company/acme"}
    ]


# --- The setup screen ---------------------------------------------------------------------


@pytest.fixture
def admin_org(org_factory, user_factory, membership_factory, client):
    org = org_factory(slug="acme", display_name="Acme")
    user = user_factory()
    membership_factory(org, user, role=MembershipRole.ADMIN)
    client.force_login(user)
    return org


@pytest.mark.django_db
def test_a_member_may_look_at_the_page_but_gets_no_form(
    org_factory, user_factory, membership_factory, client
):
    org = org_factory(slug="acme", display_name="Acme")
    user = user_factory()
    membership_factory(org, user, role=MembershipRole.MEMBER)
    client.force_login(user)
    resp = client.get(reverse("orgs:settings", kwargs={"org_slug": "acme"}))
    assert resp.status_code == 200
    assert b"Fill this in from my website" not in resp.content


@pytest.mark.django_db
def test_someone_from_another_team_cannot_see_this_page(
    org_factory, user_factory, membership_factory, client
):
    org_factory(slug="acme", display_name="Acme")
    other = org_factory(slug="other", display_name="Other")
    user = user_factory()
    membership_factory(other, user, role=MembershipRole.ADMIN)
    client.force_login(user)
    resp = client.get(reverse("orgs:settings", kwargs={"org_slug": "acme"}), follow=True)
    # Org context sends a non-member to that org's public about page, not here.
    assert resp.status_code == 200
    assert resp.redirect_chain
    assert "/settings/" not in resp.redirect_chain[-1][0]
    assert b"Fill this in from my website" not in resp.content


@pytest.mark.django_db
def test_saving_publishes_the_asks(admin_org, client):
    resp = client.post(
        reverse("orgs:settings", kwargs={"org_slug": "acme"}),
        {
            "display_name": "Acme",
            "tagline": "A thing",
            "pitch": "What we are building.",
            "looking_for": "Designer: our screens are ugly",
            "website": "acme.test",
            "logo_url": "",
            "cover_image_url": "",
            "socials": "",
        },
    )
    assert resp.status_code == 302
    admin_org.refresh_from_db()
    assert admin_org.asks == [{"role": "Designer", "detail": "our screens are ugly"}]
    assert admin_org.website == "https://acme.test"


@pytest.mark.django_db
def test_a_pull_fills_the_blanks_and_leaves_what_they_wrote_alone(admin_org, client):
    with patch.object(sitepull, "_read", lambda _u: (PAGE, "https://acme.test/")):
        resp = client.post(
            reverse("orgs:profile_pull", kwargs={"org_slug": "acme"}),
            {
                "display_name": "Acme",
                "tagline": "Our own line, keep it",
                "pitch": "",
                "looking_for": "",
                "website": "acme.test",
                "logo_url": "",
                "cover_image_url": "",
                "socials": "",
            },
        )
    assert resp.status_code == 200
    form = resp.context["form"]
    assert form.initial["tagline"] == "Our own line, keep it"
    assert form.initial["pitch"] == "Unified risk intelligence platform for site reports."
    # A pull writes nothing.
    admin_org.refresh_from_db()
    assert admin_org.pitch == ""


@pytest.mark.django_db
def test_an_unreachable_site_says_so_and_keeps_the_form(admin_org, client):
    def _boom(_u):
        raise sitepull.SiteUnreachable("We could not reach that site.")

    with patch.object(sitepull, "_read", _boom):
        resp = client.post(
            reverse("orgs:profile_pull", kwargs={"org_slug": "acme"}),
            {
                "display_name": "Acme",
                "tagline": "Our own line",
                "pitch": "",
                "looking_for": "",
                "website": "nope.test",
                "logo_url": "",
                "cover_image_url": "",
                "socials": "",
            },
        )
    assert resp.status_code == 200
    assert resp.context["form"].initial["tagline"] == "Our own line"
    assert any("could not reach" in str(m) for m in resp.context["messages"])


@pytest.mark.django_db
def test_the_page_never_shows_template_source_to_a_person(admin_org, client):
    """Django only strips {# #} comments that fit on one line; a multi-line one
    renders as literal text. One did, right above the preview."""
    resp = client.get(reverse("orgs:settings", kwargs={"org_slug": "acme"}))
    assert b"{#" not in resp.content
    assert b"{%" not in resp.content
