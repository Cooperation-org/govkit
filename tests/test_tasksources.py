"""
Tasksources tests: Taiga adapter (mocked HTTP), both valuation modes, sync idempotency,
explicit assignee identity mapping, the missing-value queue, encrypted-token round-trip,
and the DRF endpoints.

Taiga HTTP is mocked at the true boundary (``urllib.request.urlopen``) via a small URL
router, so URL/param building, pagination, status mapping, tag parsing and custom-attribute
resolution are all exercised without a live tracker.
"""

import json
from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from rest_framework.test import APIClient

from apps.orgs.models import MembershipRole, ValuationMode
from apps.tasksources import adapters, services
from apps.tasksources.models import TaskSourceConfig, TrackedTask

FERNET_KEY = Fernet.generate_key().decode()


# --- HTTP mock ------------------------------------------------------------------------


class _FakeHeaders:
    def __init__(self, data):
        self._data = data or {}

    def items(self):
        return self._data.items()


class _FakeResponse:
    def __init__(self, body, headers=None):
        self._body = json.dumps(body).encode("utf-8")
        self.headers = _FakeHeaders(headers)

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@contextmanager
def mock_taiga(routes):
    """Patch urlopen to serve canned responses. `routes`: list of (url_substr, body, headers).

    First matching substring wins, so order more specific entries first.
    """

    def fake_urlopen(request, *args, **kwargs):
        url = request.full_url
        for substr, body, headers in routes:
            if substr in url:
                return _FakeResponse(body, headers)
        raise AssertionError(f"No mock route for URL: {url}")

    with patch.object(adapters.urllib.request, "urlopen", side_effect=fake_urlopen):
        yield


def _story(sid, status_id, tags=None, assigned_to=None, username=None, points=None):
    return {
        "id": sid,
        "subject": f"Story {sid}",
        "status": status_id,
        "permalink": f"https://taiga.example/us/{sid}",
        "tags": tags or [],
        "assigned_to": assigned_to,
        "assigned_to_extra_info": {"username": username} if username else None,
        "total_points": points,
    }


# --- fixtures -------------------------------------------------------------------------


@pytest.fixture
def taiga_source(db, org_factory):
    def make(org=None, mode=ValuationMode.DIRECT_VALUE, **cfg):
        org = org or org_factory()
        org.valuation_config.valuation_mode = mode
        org.valuation_config.save()
        defaults = dict(
            org=org,
            base_url="https://taiga.example/",
            project_selector="proj",
            value_tag_pattern=r"(\d+)\s*cook",
            done_statuses=["done", "archived", "historical"],
        )
        defaults.update(cfg)
        return TaskSourceConfig.objects.create(**defaults)

    return make


# --- valuation: tag parse -------------------------------------------------------------


@pytest.mark.parametrize(
    "tags,expected",
    [
        (["5 cook"], Decimal(5)),
        (["3cook", "2 COOK"], Decimal(5)),  # summed, case-insensitive
        (["7  Cook", "urgent"], Decimal(7)),
        (["urgent", "backend"], None),  # no match -> None (goes to missing-value queue)
        ([], None),
    ],
)
def test_parse_direct_value(tags, expected):
    assert services.parse_direct_value(tags, r"(\d+)\s*cook") == expected


def test_parse_direct_value_bad_pattern_returns_none():
    assert services.parse_direct_value(["5 cook"], r"(\d+") is None


def test_parse_direct_value_empty_pattern_returns_none():
    # No greedy default: an unset pattern never matches, so nothing is auto-valued.
    assert services.parse_direct_value(["5 cook"], "") is None


def test_direct_value_unset_pattern_does_not_assign_value(
    taiga_source, user_factory, membership_factory
):
    """An unset value_tag_pattern must NOT silently assign a value; tasks go missing-value
    and the steward gets a clear config warning to set a unit-specific pattern."""
    src = taiga_source(value_tag_pattern="")
    org = src.org
    membership_factory(org=org, user=user_factory(), taiga_user_id=9)
    # A tag with a bare number ("3 priority") would be miscounted by a greedy pattern.
    stories = [_story(101, DONE, tags=[["3 priority", None]], assigned_to=9, username="alpha")]
    with mock_taiga(_base_routes(stories)):
        result = services.sync_source(src)

    task = TrackedTask.objects.get(external_id="101")
    assert task.claimed_value is None
    assert task.is_missing_value is True
    # The sync surfaces the misconfiguration rather than silently guessing.
    assert any("value_tag_pattern" in e for e in result.errors)


# --- encrypted token round-trip -------------------------------------------------------


def test_encrypted_token_roundtrip(settings, db, org_factory):
    settings.GOVKIT_SECRET_KEY = FERNET_KEY
    org = org_factory()
    src = TaskSourceConfig.objects.create(
        org=org, base_url="https://taiga.example/", api_token="s3cr3t-token"
    )
    reloaded = TaskSourceConfig.objects.get(pk=src.pk)
    assert reloaded.api_token == "s3cr3t-token"

    # Raw column is ciphertext, not the plaintext token.
    with __import__("django.db", fromlist=["connection"]).connection.cursor() as cur:
        cur.execute("SELECT api_token FROM tasksources_tasksourceconfig WHERE id=%s", [src.pk])
        raw = cur.fetchone()[0]
    assert raw != "s3cr3t-token"
    assert "s3cr3t-token" not in raw


# --- adapter: fetch + status filtering + pagination -----------------------------------


DONE = 1
NEW = 2


def _base_routes(stories, extra=None, next_page_stories=None):
    routes = [
        ("/api/v1/projects/by_slug", {"id": 7}, None),
        (
            "/api/v1/userstory-statuses",
            [
                {"id": DONE, "slug": "done"},
                {"id": NEW, "slug": "new"},
            ],
            None,
        ),
    ]
    if extra:
        routes = extra + routes
    if next_page_stories is not None:
        routes.append(("page=1", stories, {"x-pagination-next": "yes"}))
        routes.append(("page=2", next_page_stories, None))
    else:
        routes.append(("/api/v1/userstories?project=7", stories, None))
    return routes


def test_adapter_filters_by_done_status(taiga_source):
    src = taiga_source()
    stories = [
        _story(101, DONE, tags=[["5 cook", None]], assigned_to=9, username="alpha"),
        _story(102, NEW, tags=[["3 cook", None]], assigned_to=9, username="alpha"),
    ]
    with mock_taiga(_base_routes(stories)):
        dtos = adapters.get_adapter(src).fetch_tasks()
    assert [d.external_id for d in dtos] == ["101"]
    assert dtos[0].tags == ["5 cook"]
    assert dtos[0].assignee_user_id == 9
    assert dtos[0].assignee_username == "alpha"
    assert dtos[0].status_slug == "done"


def test_adapter_paginates(taiga_source):
    src = taiga_source()
    page1 = [_story(200 + i, DONE, tags=[["1 cook", None]]) for i in range(2)]
    page2 = [_story(300, DONE, tags=[["1 cook", None]])]
    with mock_taiga(_base_routes(page1, next_page_stories=page2)):
        dtos = adapters.get_adapter(src).fetch_tasks()
    assert {d.external_id for d in dtos} == {"200", "201", "300"}


def test_adapter_hours_native_points(taiga_source):
    src = taiga_source(mode=ValuationMode.HOURS_RATE, hours_field="points")
    stories = [_story(101, DONE, points="8", assigned_to=9, username="alpha")]
    with mock_taiga(_base_routes(stories)):
        dtos = adapters.get_adapter(src).fetch_tasks()
    assert dtos[0].hours == Decimal("8")


def test_adapter_hours_and_cash_custom_attribute(taiga_source):
    src = taiga_source(mode=ValuationMode.HOURS_RATE, hours_field="Hours", cash_field="Cash")
    stories = [_story(101, DONE, assigned_to=9, username="alpha")]
    extra = [
        (
            "/api/v1/userstory-custom-attributes?project=7",
            [{"id": 50, "name": "Hours"}, {"id": 51, "name": "Cash"}],
            None,
        ),
        (
            "/api/v1/userstories/custom-attributes-values/101",
            {"attributes_values": {"50": "6", "51": "120"}},
            None,
        ),
    ]
    with mock_taiga(_base_routes(stories, extra=extra)):
        dtos = adapters.get_adapter(src).fetch_tasks()
    assert dtos[0].hours == Decimal("6")
    assert dtos[0].cash == Decimal("120")


# --- sync: valuation modes, identity mapping, idempotency -----------------------------


def _member(org, user_factory, membership_factory, **identity):
    return membership_factory(org=org, user=user_factory(), **identity)


def test_sync_direct_value_maps_by_user_id(taiga_source, user_factory, membership_factory):
    src = taiga_source()
    org = src.org
    m = _member(org, user_factory, membership_factory, taiga_user_id=9)
    stories = [_story(101, DONE, tags=[["5 cook", None]], assigned_to=9, username="alpha")]
    with mock_taiga(_base_routes(stories)):
        result = services.sync_source(src)

    assert result.created == 1 and result.updated == 0 and result.unassigned == 0
    task = TrackedTask.objects.get(external_id="101")
    assert task.claimed_value == Decimal(5)
    assert task.hours is None
    assert task.assignee_id == m.pk


def test_sync_maps_by_username_when_no_user_id(taiga_source, user_factory, membership_factory):
    src = taiga_source()
    org = src.org
    m = _member(org, user_factory, membership_factory, taiga_username="Alpha")
    stories = [_story(101, DONE, tags=[["5 cook", None]], assigned_to=9, username="alpha")]
    with mock_taiga(_base_routes(stories)):
        services.sync_source(src)
    assert TrackedTask.objects.get(external_id="101").assignee_id == m.pk


def test_sync_unmapped_assignee_stays_unassigned(taiga_source):
    src = taiga_source()
    stories = [_story(101, DONE, tags=[["5 cook", None]], assigned_to=99, username="ghost")]
    with mock_taiga(_base_routes(stories)):
        result = services.sync_source(src)
    assert result.unassigned == 1
    assert TrackedTask.objects.get(external_id="101").assignee_id is None


def test_sync_is_idempotent(taiga_source, user_factory, membership_factory):
    src = taiga_source()
    _member(src.org, user_factory, membership_factory, taiga_user_id=9)
    stories = [_story(101, DONE, tags=[["5 cook", None]], assigned_to=9, username="alpha")]

    with mock_taiga(_base_routes(stories)):
        first = services.sync_source(src)
    with mock_taiga(_base_routes(stories)):
        second = services.sync_source(src)

    assert first.created == 1
    assert second.created == 0 and second.updated == 1
    assert TrackedTask.objects.filter(external_id="101").count() == 1


def test_sync_hours_rate_mode_sets_hours(taiga_source, user_factory, membership_factory):
    src = taiga_source(mode=ValuationMode.HOURS_RATE, hours_field="points")
    _member(src.org, user_factory, membership_factory, taiga_user_id=9)
    stories = [_story(101, DONE, points="8", assigned_to=9, username="alpha")]
    with mock_taiga(_base_routes(stories)):
        services.sync_source(src)
    task = TrackedTask.objects.get(external_id="101")
    assert task.hours == Decimal("8")
    assert task.claimed_value is None


# --- missing-value queue --------------------------------------------------------------


def test_missing_value_queue(taiga_source):
    src = taiga_source()
    org = src.org
    valued = [_story(101, DONE, tags=[["5 cook", None]])]
    unvalued = [_story(102, DONE, tags=[["urgent", None]])]
    with mock_taiga(_base_routes(valued + unvalued)):
        services.sync_source(src)

    missing = list(services.missing_value_tasks(org))
    assert [t.external_id for t in missing] == ["102"]
    assert TrackedTask.objects.get(external_id="102").is_missing_value is True
    assert TrackedTask.objects.get(external_id="101").is_missing_value is False


# --- DRF endpoints --------------------------------------------------------------------


def _seed_tasks(taiga_source, user_factory, membership_factory, role=MembershipRole.STEWARD):
    src = taiga_source()
    org = src.org
    user = user_factory()
    membership_factory(org=org, user=user, role=role, taiga_user_id=9)
    stories = [
        _story(101, DONE, tags=[["5 cook", None]], assigned_to=9, username="alpha"),
        _story(102, DONE, tags=[["urgent", None]], assigned_to=9, username="alpha"),
    ]
    with mock_taiga(_base_routes(stories)):
        services.sync_source(src)
    return org, user


def test_api_missing_value_endpoint(taiga_source, user_factory, membership_factory):
    org, user = _seed_tasks(taiga_source, user_factory, membership_factory)
    client = APIClient()
    client.force_login(user)
    resp = client.get(f"/api/v1/tasksources/orgs/{org.slug}/tasks/missing_value/")
    assert resp.status_code == 200
    ids = [row["external_id"] for row in resp.json()]
    assert ids == ["102"]


def test_api_unknown_org_404(taiga_source, user_factory, membership_factory):
    _org, user = _seed_tasks(taiga_source, user_factory, membership_factory)
    client = APIClient()
    client.force_login(user)
    assert client.get("/api/v1/tasksources/orgs/no-such-org/tasks/").status_code == 404


def test_api_non_member_forbidden(taiga_source, user_factory, membership_factory):
    org, _ = _seed_tasks(taiga_source, user_factory, membership_factory)
    outsider = user_factory()
    client = APIClient()
    client.force_login(outsider)
    resp = client.get(f"/api/v1/tasksources/orgs/{org.slug}/tasks/")
    assert resp.status_code == 403


def test_api_sync_requires_steward(taiga_source, user_factory, membership_factory):
    src = taiga_source()
    org = src.org
    member = user_factory()
    membership_factory(org=org, user=member, role=MembershipRole.MEMBER)
    client = APIClient()
    client.force_login(member)
    resp = client.post(f"/api/v1/tasksources/orgs/{org.slug}/sync/")
    assert resp.status_code == 403


def test_api_sync_action_runs(taiga_source, user_factory, membership_factory):
    src = taiga_source()
    org = src.org
    steward = user_factory()
    membership_factory(org=org, user=steward, role=MembershipRole.STEWARD, taiga_user_id=9)
    stories = [_story(101, DONE, tags=[["5 cook", None]], assigned_to=9, username="alpha")]
    client = APIClient()
    client.force_login(steward)
    with mock_taiga(_base_routes(stories)):
        resp = client.post(f"/api/v1/tasksources/orgs/{org.slug}/sync/")
    assert resp.status_code == 200
    assert resp.json()["sources"][0]["created"] == 1


# --- M3: api_token is never rendered in the admin change form -------------------------


@pytest.mark.django_db
def test_admin_change_form_masks_api_token(settings, org_factory, client):
    """The decrypted Fernet token must not appear in the admin change-form HTML (M3)."""
    settings.GOVKIT_SECRET_KEY = FERNET_KEY
    from django.contrib.auth import get_user_model

    User = get_user_model()
    admin_user = User.objects.create_superuser(email="root@example.com", password="pw12345!")
    org = org_factory()
    src = TaskSourceConfig.objects.create(
        org=org, base_url="https://taiga.example/", api_token="pl41nt3xt-secret-token"
    )

    client.force_login(admin_user)
    resp = client.get(f"/admin/tasksources/tasksourceconfig/{src.pk}/change/")
    assert resp.status_code == 200
    html = resp.content.decode()
    # Cleartext token is absent; the write-only input + status indicator are present.
    assert "pl41nt3xt-secret-token" not in html
    assert "api_token_input" in html
    assert "set (hidden)" in html


@pytest.mark.django_db
def test_admin_form_sets_new_token_and_keeps_old_when_blank(settings, org_factory):
    """The write-only admin form SETS a new token when provided and PRESERVES the existing
    one when left blank — so staff can rotate the token without ever reading it (M3)."""
    settings.GOVKIT_SECRET_KEY = FERNET_KEY
    from apps.tasksources.admin import TaskSourceConfigForm

    org = org_factory()
    src = TaskSourceConfig.objects.create(
        org=org, base_url="https://taiga.example/", api_token="old-token"
    )

    # Blank input leaves the existing token untouched.
    form = TaskSourceConfigForm(
        data={
            "org": org.pk,
            "adapter_type": "taiga",
            "base_url": "https://taiga.example/",
            "done_statuses": '["done"]',
            "api_token_input": "",
        },
        instance=src,
    )
    assert form.is_valid(), form.errors
    form.save()
    src.refresh_from_db()
    assert src.api_token == "old-token"

    # A non-blank input replaces it.
    form = TaskSourceConfigForm(
        data={
            "org": org.pk,
            "adapter_type": "taiga",
            "base_url": "https://taiga.example/",
            "done_statuses": '["done"]',
            "api_token_input": "rotated-token",
        },
        instance=src,
    )
    assert form.is_valid(), form.errors
    form.save()
    src.refresh_from_db()
    assert src.api_token == "rotated-token"


# --- L9: decrypt failure never returns ciphertext-as-plaintext ------------------------


@pytest.mark.django_db
def test_encrypted_field_returns_none_on_decrypt_failure(settings, org_factory, caplog):
    """If the stored ciphertext can't be decrypted (key rotated/lost), reads must NOT hand
    back the raw ciphertext as if it were the token — they return None + log an error (L9)."""
    settings.GOVKIT_SECRET_KEY = FERNET_KEY
    org = org_factory()
    src = TaskSourceConfig.objects.create(
        org=org, base_url="https://taiga.example/", api_token="s3cr3t-token"
    )

    # Simulate key rotation/loss: read under a DIFFERENT Fernet key.
    settings.GOVKIT_SECRET_KEY = Fernet.generate_key().decode()
    import logging

    with caplog.at_level(logging.ERROR):
        reloaded = TaskSourceConfig.objects.get(pk=src.pk)
        value = reloaded.api_token

    assert value is None  # fail safe: never the ciphertext, never the plaintext
    assert any("could not be decrypted" in r.message for r in caplog.records)
