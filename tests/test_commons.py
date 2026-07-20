"""Commons views (orgs / ideas / pool): login-gated for anyone invited or
signed up; ideas coalesce via support/build interest; pool = accepted pool
invites rendered with the public profile layer."""

import pytest

from apps.accounts.models import ProfileLink, ProfileLinkKind
from apps.commons.models import Idea, IdeaInterest, IdeaInterestKind
from apps.orgs.models import Invite, InviteKind, InviteStatus


@pytest.fixture
def member(user_factory):
    return user_factory(display_name="Signed Up Person")


@pytest.fixture
def logged_in(client, member):
    client.force_login(member)
    return client


class TestGating:
    @pytest.mark.parametrize("url", ["/commons/orgs/", "/commons/ideas/", "/commons/pool/"])
    def test_anonymous_redirected_to_login(self, client, db, url):
        resp = client.get(url)
        assert resp.status_code == 302
        assert "/login" in resp.url or "/accounts/" in resp.url

    @pytest.mark.parametrize("url", ["/commons/orgs/", "/commons/ideas/", "/commons/pool/"])
    def test_any_signed_up_user_can_view(self, logged_in, url):
        assert logged_in.get(url).status_code == 200


class TestOrgsView:
    def test_lists_orgs_with_member_counts(
        self, logged_in, org_factory, membership_factory, member
    ):
        org = org_factory(display_name="Test Kitchen Co-op")
        membership_factory(org, member)
        html = logged_in.get("/commons/orgs/").content.decode()
        assert "Test Kitchen Co-op" in html


class TestIdeas:
    def test_post_idea_and_render(self, logged_in, member):
        logged_in.post("/commons/ideas/new/", {"title": "Tool library", "pitch": "Shared tools."})
        idea = Idea.objects.get()
        assert idea.created_by == member
        assert idea.slug == "tool-library"
        html = logged_in.get("/commons/ideas/").content.decode()
        assert "Tool library" in html
        assert "Shared tools." in html

    def test_blank_idea_rejected(self, logged_in):
        logged_in.post("/commons/ideas/new/", {"title": "  ", "pitch": ""})
        assert Idea.objects.count() == 0

    def test_slug_collision_gets_suffix(self, member, db):
        Idea.objects.create(title="Same", pitch="a", created_by=member)
        second = Idea.objects.create(title="Same", pitch="b", created_by=member)
        assert second.slug == "same-2"

    def test_interest_declare_change_withdraw(self, logged_in, member, user_factory):
        idea = Idea.objects.create(title="X", pitch="p", created_by=user_factory())
        url = f"/commons/ideas/{idea.slug}/interest/"
        logged_in.post(url, {"kind": "support"})
        assert IdeaInterest.objects.get(user=member).kind == IdeaInterestKind.SUPPORT
        logged_in.post(url, {"kind": "build"})  # change
        assert IdeaInterest.objects.get(user=member).kind == IdeaInterestKind.BUILD
        logged_in.post(url, {"kind": "build"})  # same again = withdraw
        assert IdeaInterest.objects.count() == 0

    def test_interest_bad_kind_ignored(self, logged_in, member, user_factory):
        idea = Idea.objects.create(title="Y", pitch="p", created_by=user_factory())
        logged_in.post(f"/commons/ideas/{idea.slug}/interest/", {"kind": "hostile"})
        assert IdeaInterest.objects.count() == 0

    def test_inactive_idea_hidden(self, logged_in, member):
        Idea.objects.create(title="Retired", pitch="p", created_by=member, is_active=False)
        assert "Retired" not in logged_in.get("/commons/ideas/").content.decode()


class TestPool:
    def _pool_accept(self, org, user, **kw):
        return Invite.objects.create(
            org=org,
            kind=InviteKind.POOL,
            status=InviteStatus.ACCEPTED,
            accepted_by=user,
            **kw,
        )

    def test_lists_accepted_pool_people_with_public_links(
        self, logged_in, org_factory, user_factory
    ):
        org = org_factory()
        person = user_factory(display_name="Pool Person", bio="I cook.")
        ProfileLink.objects.create(
            user=person, kind=ProfileLinkKind.BLUESKY, handle="@pool.bsky.social", is_public=True
        )
        ProfileLink.objects.create(
            user=person, kind=ProfileLinkKind.WEBSITE, url="https://secret.example.com"
        )
        self._pool_accept(org, person)
        html = logged_in.get("/commons/pool/").content.decode()
        assert "Pool Person" in html
        assert "I cook." in html
        assert "@pool.bsky.social" in html
        assert "secret.example.com" not in html  # private link stays private

    def test_org_invites_and_unaccepted_pool_invites_excluded(
        self, logged_in, org_factory, user_factory
    ):
        org = org_factory()
        joined = user_factory(display_name="Org Joiner")
        Invite.objects.create(
            org=org, kind=InviteKind.ORG, status=InviteStatus.ACCEPTED, accepted_by=joined
        )
        Invite.objects.create(org=org, kind=InviteKind.POOL)  # minted, not accepted
        html = logged_in.get("/commons/pool/").content.decode()
        assert "Org Joiner" not in html
        assert "No one in the pool yet" in html
