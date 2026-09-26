"""A person's own dashboard arrangement: GET/PUT/DELETE /api/v1/accounts/me/layouts/<dash>/."""

import pytest

from apps.accounts.models import DashLayout

URL = "/api/v1/accounts/me/layouts/cohort/"
EMBED = {"HTTP_X_GOVKIT_EMBED": "1"}


@pytest.fixture
def person(django_user_model):
    return django_user_model.objects.create_user(email="a@example.com", password="x")


@pytest.mark.django_db
def test_signed_out_is_refused(client):
    assert client.get(URL, **EMBED).status_code in (401, 403)


@pytest.mark.django_db
def test_default_is_empty_then_saved_then_reset(client, person):
    client.force_login(person)
    assert client.get(URL, **EMBED).json() == {"layout": {}}

    layout = {"items": [{"id": "pie", "x": 0, "y": 0, "w": 6}], "hidden": ["news"]}
    r = client.put(URL, {"layout": layout}, content_type="application/json", **EMBED)
    assert r.status_code == 200
    assert client.get(URL, **EMBED).json() == {"layout": layout}
    assert DashLayout.objects.get(user=person, dashboard="cohort").layout == layout

    assert client.delete(URL, **EMBED).status_code == 200
    assert client.get(URL, **EMBED).json() == {"layout": {}}


@pytest.mark.django_db
def test_write_needs_embed_header(client, person):
    client.force_login(person)
    r = client.put(URL, {"layout": {}}, content_type="application/json")
    assert r.status_code == 403


@pytest.mark.django_db
def test_rejects_non_object_and_oversize(client, person):
    client.force_login(person)
    bad = client.put(URL, {"layout": [1]}, content_type="application/json", **EMBED)
    assert bad.status_code == 400
    big = {"x": "a" * 40_000}
    assert client.put(URL, {"layout": big}, content_type="application/json", **EMBED).status_code == 400


@pytest.mark.django_db
def test_one_person_cannot_see_another(client, person, django_user_model):
    other = django_user_model.objects.create_user(email="b@example.com", password="x")
    DashLayout.objects.create(user=other, dashboard="cohort", layout={"items": []})
    client.force_login(person)
    assert client.get(URL, **EMBED).json() == {"layout": {}}
