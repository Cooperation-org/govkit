"""
CORS tests (PLAN-cohort-dash.md item 1): a configured origin gets credentialed CORS
headers on /api/ paths ONLY — HTML pages are never CORS-exposed, unlisted origins get
nothing. All requests here are anonymous, so no database is touched: this file runs
even where test-database creation is unavailable.
"""

import pytest

ORIGIN = "https://dash.example"


@pytest.fixture
def cors(settings):
    settings.CORS_ALLOWED_ORIGINS = [ORIGIN]
    return settings


def test_allowed_origin_gets_credentialed_cors_on_api(client, cors):
    resp = client.get("/api/v1/accounts/me/", HTTP_ORIGIN=ORIGIN)
    # Anonymous -> DRF 403; CORS headers are present regardless of status.
    assert resp["Access-Control-Allow-Origin"] == ORIGIN
    assert resp["Access-Control-Allow-Credentials"] == "true"


def test_preflight_succeeds_for_allowed_origin(client, cors):
    resp = client.options(
        "/api/v1/accounts/me/",
        HTTP_ORIGIN=ORIGIN,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
    )
    assert resp.status_code == 200
    assert resp["Access-Control-Allow-Origin"] == ORIGIN
    assert resp["Access-Control-Allow-Credentials"] == "true"


def test_unlisted_origin_gets_no_cors_headers(client, cors):
    resp = client.get("/api/v1/accounts/me/", HTTP_ORIGIN="https://evil.example")
    assert "Access-Control-Allow-Origin" not in resp


def test_non_api_paths_are_not_cors_exposed(client, cors):
    resp = client.get("/accounts/login/", HTTP_ORIGIN=ORIGIN)
    assert resp.status_code == 200
    assert "Access-Control-Allow-Origin" not in resp


def test_no_configured_origins_means_no_cors(client, settings):
    settings.CORS_ALLOWED_ORIGINS = []
    resp = client.get("/api/v1/accounts/me/", HTTP_ORIGIN=ORIGIN)
    assert "Access-Control-Allow-Origin" not in resp
