"""
Base-path support.

When the app is deployed behind a path prefix (BASE_PATH -> FORCE_SCRIPT_NAME), a real
WSGI server sets the URL script prefix per request, so every {% url %} / {% static %}
gains the prefix. The Django test client's ClientHandler does NOT set that prefix, so we
verify the underlying mechanism directly (reverse + static under a script prefix) plus a
plain-render smoke test. The end-to-end prefixed render is verified against a live server
in the build's manual verification step.
"""

import pytest
from django.templatetags.static import static
from django.test import override_settings
from django.urls import get_script_prefix, reverse, set_script_prefix


@pytest.mark.django_db
def test_landing_renders(client):
    resp = client.get(reverse("orgs:landing"))
    assert resp.status_code == 200


def test_reverse_carries_script_prefix():
    old = get_script_prefix()
    try:
        set_script_prefix("/govkit/")
        assert reverse("orgs:landing") == "/govkit/"
        assert reverse("drops:index", kwargs={"org_slug": "mine"}) == "/govkit/o/mine/drops/"
        assert reverse("pie:index", kwargs={"org_slug": "mine"}) == "/govkit/o/mine/pie/"
    finally:
        set_script_prefix(old)


@override_settings(STATIC_URL="/govkit/static/")
def test_static_url_carries_base_path():
    assert static("govkit.css") == "/govkit/static/govkit.css"
