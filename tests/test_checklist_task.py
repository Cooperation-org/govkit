"""
The checklist item sheet: what a team writes about an item goes onto their own
board, as a task, and comes back when they open the item again.

The tracker is mocked throughout — these tests are about the pointer between an
item and a story, and about not creating stories nobody asked for.
"""

import urllib.error

import pytest
from unittest.mock import patch

from apps.orgs.genesis import MODULES, start_genesis
from apps.orgs.models import ChecklistTask
from apps.tasksources.adapters import TaskDetailDTO
from apps.tasksources.models import TaskSourceConfig

FIRST_KEY = MODULES[0][3][0][0]
FIRST_TITLE = MODULES[0][3][0][1]


@pytest.fixture
def team(org_factory, user_factory, membership_factory):
    org = org_factory()
    user = user_factory()
    membership = membership_factory(org, user)
    start_genesis(org)
    return org, user, membership


@pytest.fixture
def outsider(user_factory):
    return user_factory(email="outsider@example.com")


class _FakeAdapter:
    """A board that remembers one story, so create-then-edit can be observed."""

    def __init__(self, fetch_error=None):
        self.stories = {}
        self.created = []
        self.fetch_error = fetch_error
        self._next = 100

    def create_task(self, subject, description=""):
        self._next += 1
        external_id = str(self._next)
        detail = TaskDetailDTO(
            external_id=external_id,
            subject=subject,
            description=description,
            ref=self._next,
            project_slug="acme-board",
            version=1,
        )
        self.stories[external_id] = detail
        self.created.append(subject)
        return detail

    def fetch_task(self, external_id):
        if self.fetch_error is not None:
            raise self.fetch_error
        if external_id not in self.stories:
            raise LookupError(external_id)
        return self.stories[external_id]

    def update_task(self, external_id, subject=None, description=None, version=None):
        detail = self.stories[external_id]
        if subject is not None:
            detail.subject = subject
        if description is not None:
            detail.description = description
        return detail


def _source(org):
    return TaskSourceConfig.objects.create(org=org, base_url="https://tracker.example/api")


def _url(org, item_key=FIRST_KEY):
    return f"/api/v1/tasksources/orgs/{org.slug}/checklist/{item_key}/"


def _read(client, org, item_key=FIRST_KEY):
    """The dash sends the embed header on reads too (apps.orgs.embed_auth)."""
    return client.get(_url(org, item_key), HTTP_X_GOVKIT_EMBED="1")


def _write(client, org, text, item_key=FIRST_KEY):
    return client.post(
        _url(org, item_key),
        data={"description": text},
        content_type="application/json",
        HTTP_X_GOVKIT_EMBED="1",
    )


def test_nothing_written_yet_is_a_404_not_an_error(client, team):
    """Opening an item nobody has answered must not look like a failure."""
    org, user, _ = team
    _source(org)
    client.force_login(user)
    with patch("apps.tasksources.api.get_adapter", return_value=_FakeAdapter()):
        assert _read(client, org).status_code == 404


def test_opening_an_item_creates_nothing(client, team):
    """A team reading the curriculum does not end up with a board full of stubs."""
    org, user, _ = team
    _source(org)
    client.force_login(user)
    fake = _FakeAdapter()
    with patch("apps.tasksources.api.get_adapter", return_value=fake):
        _read(client, org)
    assert fake.created == []
    assert not ChecklistTask.objects.filter(org=org).exists()


def test_first_save_creates_the_task_titled_as_the_item(client, team):
    org, user, _ = team
    _source(org)
    client.force_login(user)
    fake = _FakeAdapter()
    with patch("apps.tasksources.api.get_adapter", return_value=fake):
        resp = _write(client, org, "We talked to three people.")

    assert resp.status_code == 201
    assert resp.json()["description"] == "We talked to three people."
    assert fake.created == [FIRST_TITLE]
    link = ChecklistTask.objects.get(org=org, item_key=FIRST_KEY)
    assert link.external_id == resp.json()["external_id"]
    assert link.created_by == user


def test_second_save_edits_the_same_task(client, team):
    """Come back next week and add to it, rather than starting another story."""
    org, user, _ = team
    _source(org)
    client.force_login(user)
    fake = _FakeAdapter()
    with patch("apps.tasksources.api.get_adapter", return_value=fake):
        first = _write(client, org, "First pass.")
        second = _write(client, org, "First pass. Then more.")

    assert second.status_code == 200
    assert second.json()["external_id"] == first.json()["external_id"]
    assert len(fake.created) == 1
    assert ChecklistTask.objects.filter(org=org, item_key=FIRST_KEY).count() == 1


def test_the_words_come_back_when_the_item_is_opened_again(client, team):
    org, user, _ = team
    _source(org)
    client.force_login(user)
    fake = _FakeAdapter()
    with patch("apps.tasksources.api.get_adapter", return_value=fake):
        _write(client, org, "What they actually said.")
        resp = _read(client, org)

    assert resp.status_code == 200
    assert resp.json()["description"] == "What they actually said."


def test_the_checklist_says_which_items_have_words(client, team):
    """The dash marks an item you have written on, without opening every one."""
    org, user, _ = team
    _source(org)
    client.force_login(user)
    fake = _FakeAdapter()
    with patch("apps.tasksources.api.get_adapter", return_value=fake):
        _write(client, org, "Written.")

    items = client.get(f"/api/v1/orgs/{org.slug}/checklist/").json()["modules"][0]["items"]
    marked = {i["key"]: i["has_note"] for i in items}
    assert marked[FIRST_KEY] is True
    assert all(v is False for k, v in marked.items() if k != FIRST_KEY)


def test_a_story_deleted_from_the_board_starts_a_fresh_one(client, team):
    """A dead pointer must not fail forever; the item is still answerable."""
    org, user, _ = team
    _source(org)
    client.force_login(user)
    fake = _FakeAdapter()
    with patch("apps.tasksources.api.get_adapter", return_value=fake):
        _write(client, org, "First.")
        fake.stories.clear()  # somebody deleted it on the board
        resp = _write(client, org, "Second.")

    assert resp.status_code == 201
    assert len(fake.created) == 2
    assert ChecklistTask.objects.get(org=org, item_key=FIRST_KEY).external_id in fake.stories


def test_no_board_connected_says_where_to_go(client, team):
    org, user, _ = team  # no TaskSourceConfig
    client.force_login(user)
    resp = _write(client, org, "Anything.")
    assert resp.status_code == 409
    assert "Settings" in resp.json()["detail"]
    assert not ChecklistTask.objects.filter(org=org).exists()


def test_tracker_down_does_not_leave_a_dangling_pointer(client, team):
    org, user, _ = team
    _source(org)
    client.force_login(user)
    fake = _FakeAdapter()
    with patch.object(fake, "create_task", side_effect=urllib.error.URLError("down")):
        with patch("apps.tasksources.api.get_adapter", return_value=fake):
            resp = _write(client, org, "Anything.")
    assert resp.status_code == 502
    assert not ChecklistTask.objects.filter(org=org).exists()


def test_unknown_item_key_404s(client, team):
    org, user, _ = team
    _source(org)
    client.force_login(user)
    fake = _FakeAdapter()
    with patch("apps.tasksources.api.get_adapter", return_value=fake):
        resp = _write(client, org, "Anything.", item_key="exist.no-such-item")
    assert resp.status_code == 404
    assert fake.created == []


def test_non_member_cannot_write_on_a_teams_checklist(client, team, outsider):
    org, _, _ = team
    _source(org)
    client.force_login(outsider)
    assert _write(client, org, "Anything.").status_code == 403


def test_signed_out_gets_a_status_not_a_login_page(client, team):
    org, _, _ = team
    _source(org)
    assert _read(client, org).status_code == 401
