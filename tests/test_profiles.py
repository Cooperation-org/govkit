"""Profile schema + public profile API (design doc 2026-07-20: unlimited links
via related table; every link opt-in public; typed kind + parseable handle)."""

import pytest
from django.db import IntegrityError, transaction

from apps.accounts.models import ProfileLink, ProfileLinkKind


@pytest.fixture
def lt_user(user_factory):
    return user_factory(
        display_name="Pat Example",
        auth_provider="linkedtrust",
        auth_provider_id="lt-sub-123",
        bio="Builds kitchens.",
    )


def _link(user, **kw):
    defaults = {"kind": ProfileLinkKind.WEBSITE, "url": "https://example.com"}
    defaults.update(kw)
    return ProfileLink.objects.create(user=user, **defaults)


class TestProfileLinkModel:
    def test_unlimited_links_per_user(self, lt_user):
        for i in range(15):
            _link(lt_user, url=f"https://site{i}.example.com", order=i)
        assert lt_user.profile_links.count() == 15

    def test_url_or_handle_required(self, lt_user):
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ProfileLink.objects.create(user=lt_user, kind=ProfileLinkKind.WEBSITE)

    def test_handle_only_is_valid(self, lt_user):
        link = _link(lt_user, kind=ProfileLinkKind.BLUESKY, url="", handle="@pat.bsky.social")
        assert link.pk

    def test_ordering_by_order_then_id(self, lt_user):
        second = _link(lt_user, url="https://b.example.com", order=2)
        first = _link(lt_user, url="https://a.example.com", order=1)
        assert list(lt_user.profile_links.all()) == [first, second]

    def test_public_defaults_off(self, lt_user):
        assert _link(lt_user).is_public is False


class TestPublicProfileAPI:
    URL = "/api/v1/accounts/profiles/linkedtrust/lt-sub-123/"

    def test_returns_public_layer_only(self, client, lt_user):
        _link(lt_user, url="https://public.example.com", is_public=True, order=1)
        _link(lt_user, url="https://private.example.com", is_public=False, order=2)
        resp = client.get(self.URL)
        assert resp.status_code == 200
        data = resp.json()
        assert data["display_name"] == "Pat Example"
        assert data["bio"] == "Builds kitchens."
        urls = [link["url"] for link in data["links"]]
        assert urls == ["https://public.example.com"]

    def test_no_auth_required(self, client, lt_user):
        assert client.get(self.URL).status_code == 200

    def test_unknown_subject_404(self, client, db):
        assert client.get("/api/v1/accounts/profiles/linkedtrust/nope/").status_code == 404

    def test_inactive_user_404(self, client, lt_user):
        lt_user.is_active = False
        lt_user.save()
        assert client.get(self.URL).status_code == 404

    def test_email_never_in_payload(self, client, lt_user):
        _link(lt_user, is_public=True)
        body = client.get(self.URL).json()
        assert "email" not in body
        assert lt_user.email not in str(body)
