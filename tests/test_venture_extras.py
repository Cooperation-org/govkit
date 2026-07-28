"""The three lists a team fills in for its own public page.

Pictures, things they made, and what they are up to. Each is a list of rows an
admin adds one at a time, and the rules that matter are about what a STRANGER
ends up seeing: a feed that would look abandoned is not shown at all, and a
calendar the team did not make public never leaves GovKit.
"""

from datetime import date, timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.orgs.models import OrgLink, OrgPicture, OrgPost


@pytest.fixture
def bucket(settings):
    settings.GOVKIT_STORAGE_BUCKET = "bucket"
    settings.GOVKIT_STORAGE_KEY = "k"
    settings.GOVKIT_STORAGE_SECRET = "s"
    return settings


@pytest.fixture
def admin_org(org_factory, user_factory, membership_factory, client):
    org = org_factory(slug="acme", display_name="Acme")
    user = user_factory()
    membership_factory(org=org, user=user, role="admin")
    client.force_login(user)
    return org


@pytest.fixture
def member_org(org_factory, user_factory, membership_factory, client):
    org = org_factory(slug="bcme", display_name="Bcme")
    user = user_factory()
    membership_factory(org=org, user=user, role="member")
    client.force_login(user)
    return org


def a_picture(name="shot.png"):
    return SimpleUploadedFile(name, b"\x89PNG\r\n\x1a\n" + b"0" * 200, content_type="image/png")


# --- pictures ---------------------------------------------------------------


@pytest.mark.django_db
def test_a_picture_can_be_uploaded_and_gets_a_thumbnail(client, admin_org, bucket):
    url = reverse("orgs:picture_add", kwargs={"org_slug": admin_org.slug})
    with pytest.MonkeyPatch.context() as m:
        m.setattr(
            "apps.commons.storage.store_image_pair",
            lambda upload, prefix: (f"https://cdn/{prefix}/big.png", f"https://cdn/{prefix}/s.png"),
        )
        client.post(url, {"picture": a_picture(), "caption": "The crew"})
    pic = OrgPicture.objects.get(org=admin_org)
    assert pic.caption == "The crew"
    assert pic.url.endswith("big.png")
    assert pic.grid_url.endswith("s.png")


@pytest.mark.django_db
def test_a_picture_can_be_a_link_instead(client, admin_org):
    client.post(
        reverse("orgs:picture_add", kwargs={"org_slug": admin_org.slug}),
        {"picture_url": "acme.test/shot.png"},
    )
    pic = OrgPicture.objects.get(org=admin_org)
    assert pic.url == "https://acme.test/shot.png"
    # No second copy to make, so the picture is its own thumbnail.
    assert pic.grid_url == pic.url


@pytest.mark.django_db
def test_a_picture_with_neither_file_nor_link_is_refused(client, admin_org):
    client.post(reverse("orgs:picture_add", kwargs={"org_slug": admin_org.slug}), {})
    assert not OrgPicture.objects.filter(org=admin_org).exists()


@pytest.mark.django_db
def test_a_picture_can_be_taken_off_the_page(client, admin_org):
    pic = OrgPicture.objects.create(org=admin_org, url="https://acme.test/x.png")
    client.post(
        reverse("orgs:picture_remove", kwargs={"org_slug": admin_org.slug, "picture_id": pic.id})
    )
    assert not OrgPicture.objects.filter(pk=pic.pk).exists()


@pytest.mark.django_db
def test_a_member_who_is_not_an_admin_cannot_add_a_picture(client, member_org):
    r = client.post(
        reverse("orgs:picture_add", kwargs={"org_slug": member_org.slug}),
        {"picture_url": "https://bcme.test/x.png"},
    )
    assert r.status_code == 403
    assert not OrgPicture.objects.filter(org=member_org).exists()


@pytest.mark.django_db
def test_pictures_keep_the_order_they_were_added(client, admin_org):
    for n in ("one", "two", "three"):
        client.post(
            reverse("orgs:picture_add", kwargs={"org_slug": admin_org.slug}),
            {"picture_url": f"https://acme.test/{n}.png", "caption": n},
        )
    assert [p.caption for p in admin_org.pictures.all()] == ["one", "two", "three"]


# --- things they made -------------------------------------------------------


@pytest.mark.django_db
def test_a_link_shows_its_bare_host(client, admin_org):
    client.post(
        reverse("orgs:link_add", kwargs={"org_slug": admin_org.slug}),
        {"title": "Street Math", "url": "https://www.street.riskrunners.com/"},
    )
    link = OrgLink.objects.get(org=admin_org)
    assert link.title == "Street Math"
    assert link.host == "street.riskrunners.com"


@pytest.mark.django_db
def test_a_link_needs_a_name_and_a_url(client, admin_org):
    client.post(reverse("orgs:link_add", kwargs={"org_slug": admin_org.slug}), {"title": "Deck"})
    assert not OrgLink.objects.filter(org=admin_org).exists()


@pytest.mark.django_db
def test_a_link_may_have_no_picture(client, admin_org):
    client.post(
        reverse("orgs:link_add", kwargs={"org_slug": admin_org.slug}),
        {"title": "Deck", "url": "acme.test/deck"},
    )
    link = OrgLink.objects.get(org=admin_org)
    assert link.image_url == ""
    assert link.url == "https://acme.test/deck"


# --- what they are up to ----------------------------------------------------


def post_on(org, days_ago, words="something"):
    return OrgPost.objects.create(
        org=org, words=words, happened_on=date.today() - timedelta(days=days_ago)
    )


@pytest.mark.django_db
def test_two_posts_are_not_a_feed(admin_org):
    post_on(admin_org, 1)
    post_on(admin_org, 2)
    assert admin_org.public_posts() == []


@pytest.mark.django_db
def test_three_recent_posts_are(admin_org):
    for days in (1, 5, 9):
        post_on(admin_org, days)
    assert len(admin_org.public_posts()) == 3


@pytest.mark.django_db
def test_a_feed_that_stopped_months_ago_is_not_shown(admin_org):
    for days in (95, 120, 200):
        post_on(admin_org, days)
    assert admin_org.public_posts() == []


@pytest.mark.django_db
def test_the_newest_post_comes_first(admin_org):
    post_on(admin_org, 30, "older")
    post_on(admin_org, 1, "newest")
    post_on(admin_org, 10, "middle")
    assert [p.words for p in admin_org.public_posts()] == ["newest", "middle", "older"]


@pytest.mark.django_db
def test_a_post_with_no_date_happened_today(client, admin_org):
    client.post(
        reverse("orgs:post_add", kwargs={"org_slug": admin_org.slug}),
        {"words": "The map is live."},
    )
    assert OrgPost.objects.get(org=admin_org).happened_on == date.today()


@pytest.mark.django_db
def test_a_post_needs_words(client, admin_org):
    client.post(reverse("orgs:post_add", kwargs={"org_slug": admin_org.slug}), {"link_url": "x.com"})
    assert not OrgPost.objects.filter(org=admin_org).exists()


# --- what leaves GovKit -----------------------------------------------------


@pytest.mark.django_db
def test_a_private_calendar_is_not_in_the_public_card(admin_org):
    from apps.orgs.api import _venture_card

    admin_org.calendar_url = "https://cal.test/acme"
    admin_org.calendar_public = False
    admin_org.member_count = 1
    assert _venture_card(admin_org)["calendar_url"] == ""


@pytest.mark.django_db
def test_a_public_calendar_is(admin_org):
    from apps.orgs.api import _venture_card

    admin_org.calendar_url = "https://cal.test/acme"
    admin_org.calendar_public = True
    admin_org.member_count = 1
    assert _venture_card(admin_org)["calendar_url"] == "https://cal.test/acme"


@pytest.mark.django_db
def test_the_public_card_carries_the_three_lists(admin_org):
    from apps.orgs.api import _venture_card

    OrgPicture.objects.create(org=admin_org, url="https://acme.test/1.png", thumb_url="")
    OrgLink.objects.create(org=admin_org, title="Deck", url="https://acme.test/deck")
    for days in (1, 2, 3):
        post_on(admin_org, days)
    admin_org.member_count = 1
    card = _venture_card(admin_org)
    assert len(card["pictures"]) == 1
    assert card["links"][0]["host"] == "acme.test"
    assert len(card["posts"]) == 3


@pytest.mark.django_db
def test_the_public_card_sends_no_posts_when_there_is_no_feed(admin_org):
    from apps.orgs.api import _venture_card

    post_on(admin_org, 1)
    admin_org.member_count = 1
    assert _venture_card(admin_org)["posts"] == []


# --- what we keep in the bucket ---------------------------------------------


def test_a_big_picture_is_shrunk_before_it_is_stored():
    from io import BytesIO

    from PIL import Image

    from apps.commons import storage

    out = BytesIO()
    Image.new("RGB", (4000, 3000), "white").save(out, format="JPEG")
    shrunk = storage._shrink(out.getvalue(), "image/jpeg", storage.PAGE_WIDE)
    assert shrunk is not None
    assert max(Image.open(BytesIO(shrunk[0])).size) == storage.PAGE_WIDE


def test_a_small_picture_is_left_alone():
    from io import BytesIO

    from PIL import Image

    from apps.commons import storage

    out = BytesIO()
    Image.new("RGB", (600, 400), "white").save(out, format="JPEG")
    assert storage._shrink(out.getvalue(), "image/jpeg", storage.PAGE_WIDE) is None


def test_an_animated_gif_is_never_resized():
    from apps.commons import storage

    assert storage._shrink(b"GIF89a" + b"0" * 100, "image/gif", storage.PAGE_WIDE) is None


def test_the_limit_a_person_is_told_is_the_limit_we_enforce():
    from apps.commons import storage

    class Big:
        size = storage.MAX_BYTES + 1
        content_type = "image/jpeg"

    assert f"{storage.MAX_BYTES // 1024 // 1024}MB" in storage.check_image(Big())
