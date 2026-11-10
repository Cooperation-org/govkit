"""
Tool access (apps.access): who may use the team's Taiga boards and CRM.

HTTP is mocked at the true boundary: ``urllib.request.urlopen`` for Taiga and
``xmlrpc.client.ServerProxy`` for Odoo. No live tool is ever contacted.
"""

import json
import urllib.error
import urllib.parse
from contextlib import contextmanager
from io import BytesIO
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from django.db import connection as db_connection
from django.urls import reverse

from apps.access import adapters, services
from apps.access.adapters import ToolError
from apps.access.models import AccessChange, ToolConnection
from apps.orgs.models import MembershipRole

FERNET_KEY = Fernet.generate_key().decode()
EMBED = {"HTTP_X_GOVKIT_EMBED": "1"}


@pytest.fixture(autouse=True)
def _key(settings):
    settings.GOVKIT_SECRET_KEY = FERNET_KEY


@pytest.fixture
def org(org_factory):
    return org_factory(slug="team")


def taiga_conn(org, **kw):
    return ToolConnection.objects.create(
        org=org,
        kind="taiga",
        display_name="Boards",
        base_url="https://taiga.example",
        admin_login="bot",
        admin_secret="pw",
        default_role="Back",
        **kw,
    )


def odoo_conn(org, **kw):
    return ToolConnection.objects.create(
        org=org,
        kind="odoo",
        display_name="CRM",
        base_url="https://crm.example",
        database="crm",
        admin_login="admin@example.com",
        admin_secret="pw",
        **kw,
    )


# --- models ---------------------------------------------------------------------------


def test_secret_encrypted_at_rest(org):
    conn = taiga_conn(org)
    assert ToolConnection.objects.get(pk=conn.pk).admin_secret == "pw"
    with db_connection.cursor() as cur:
        cur.execute("SELECT admin_secret FROM access_toolconnection WHERE id=%s", [conn.pk])
        raw = cur.fetchone()[0]
    assert raw and "pw" != raw


def test_clean_requires_odoo_database_and_taiga_role(org):
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        ToolConnection(org=org, kind="odoo", display_name="x", base_url="https://x.example",
                       admin_login="a").full_clean()
    with pytest.raises(ValidationError):
        ToolConnection(org=org, kind="taiga", display_name="x", base_url="https://x.example",
                       admin_login="a").full_clean()


def test_access_change_is_append_only(org, user_factory):
    change = AccessChange.objects.create(
        org=org, connection=taiga_conn(org), email="a@example.com", action="grant", result="ok"
    )
    with pytest.raises(ValueError):
        change.save()


# --- Taiga adapter --------------------------------------------------------------------


class _Resp:
    def __init__(self, body):
        self._b = b"" if body is None else json.dumps(body).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeTaiga:
    """A tiny Taiga: two boards (one archived), memberships by project id."""

    def __init__(self, post_status=201):
        self.projects = [
            {"id": 1, "slug": "app", "name": "App", "i_am_admin": True},
            {"id": 2, "slug": "old", "name": "Old", "is_archived": True, "i_am_admin": True},
            {"id": 3, "slug": "theirs", "name": "Theirs", "i_am_admin": False},
        ]
        self.members = {
            1: [
                {"id": 10, "user_email": "bot@example.com", "full_name": "Bot"},
                {"id": 11, "user_email": "Ann@Example.com", "full_name": "Ann A"},
            ]
        }
        self.post_status = post_status
        self.calls = []
        self.next_id = 100

    def __call__(self, request, timeout=None):
        url = urllib.parse.urlparse(request.full_url)
        q = dict(urllib.parse.parse_qsl(url.query))
        path = url.path.replace("/api/v1/", "")
        body = json.loads(request.data) if request.data else None
        self.calls.append((request.get_method(), path, q, body))
        method = request.get_method()
        if path == "auth":
            if body["password"] != "pw":
                raise urllib.error.HTTPError(request.full_url, 401, "no", {}, BytesIO())
            return _Resp({"auth_token": "t", "id": 5, "email": "bot@example.com",
                          "username": "bot"})
        assert request.headers["Authorization"] == "Bearer t"
        if path == "projects":
            return _Resp(self.projects)
        if path == "memberships" and method == "GET":
            return _Resp(self.members.get(int(q["project"]), []))
        if path == "roles":
            return _Resp([{"id": 7, "name": "Front"}, {"id": 8, "name": "Back"}])
        if path == "memberships" and method == "POST":
            self.next_id += 1
            if self.post_status != "drop":
                self.members.setdefault(body["project"], []).append(
                    {"id": self.next_id, "email": body["username"], "full_name": ""}
                )
            if self.post_status != 201:
                code = 500 if self.post_status == "drop" else self.post_status
                raise urllib.error.HTTPError(request.full_url, code, "err", {}, BytesIO())
            return _Resp({"id": self.next_id})
        if path.startswith("memberships/") and method == "DELETE":
            mid = int(path.split("/")[1])
            for pid, rows in self.members.items():
                self.members[pid] = [m for m in rows if m["id"] != mid]
            return _Resp(None)
        raise AssertionError(f"unexpected {method} {path}")


@contextmanager
def taiga(fake):
    with patch.object(adapters.urllib.request, "urlopen", side_effect=fake):
        yield fake


def test_taiga_read_lists_active_managed_boards_and_members(org):
    with taiga(FakeTaiga()) as fake:
        scopes, people = adapters.adapter_for(taiga_conn(org)).read()
    assert scopes == [{"slug": "app", "name": "App"}]
    assert people["ann@example.com"] == {"name": "Ann A", "scopes": ["app"]}
    assert ("GET", "projects", {"member": "5"}, None) in fake.calls


def test_taiga_grant_trusts_live_list_over_500(org):
    # Taiga 500s when its invite email fails, yet the membership exists.
    with taiga(FakeTaiga(post_status=500)) as fake:
        detail = adapters.adapter_for(taiga_conn(org)).grant("new@example.com", "app")
    assert "member" in detail
    post = [c for c in fake.calls if c[0] == "POST" and c[1] == "memberships"][0]
    assert post[3] == {"project": 1, "role": 8, "username": "new@example.com"}


def test_taiga_grant_fails_when_membership_missing(org):
    with taiga(FakeTaiga(post_status="drop")):
        with pytest.raises(ToolError):
            adapters.adapter_for(taiga_conn(org)).grant("new@example.com", "app")


def test_taiga_grant_refuses_unknown_or_archived_board(org):
    with taiga(FakeTaiga()):
        with pytest.raises(ToolError):
            adapters.adapter_for(taiga_conn(org)).grant("new@example.com", "old")


def test_taiga_grant_without_the_role_fails(org):
    conn = taiga_conn(org)
    conn.default_role = "Owner"
    with taiga(FakeTaiga()):
        with pytest.raises(ToolError, match="Owner"):
            adapters.adapter_for(conn).grant("new@example.com", "app")


def test_taiga_revoke_deletes_membership(org):
    with taiga(FakeTaiga()) as fake:
        detail = adapters.adapter_for(taiga_conn(org)).revoke("ann@example.com", "app")
        assert detail == "membership removed"
        assert ("DELETE", "memberships/11", {}, None) in fake.calls


def test_taiga_never_revokes_its_own_login(org):
    with taiga(FakeTaiga()):
        with pytest.raises(ToolError):
            adapters.adapter_for(taiga_conn(org)).revoke("bot@example.com", "app")


def test_taiga_bad_password_is_a_tool_error(org):
    conn = taiga_conn(org)
    conn.admin_secret = "wrong"
    with taiga(FakeTaiga()):
        with pytest.raises(ToolError, match="401"):
            adapters.adapter_for(conn).read()


# --- Odoo adapter ---------------------------------------------------------------------


class FakeOdoo:
    """Stands in for both XML-RPC endpoints (common and object)."""

    def __init__(self, uid=2):
        self.uid = uid
        self.users = [
            {"id": 2, "login": "admin@example.com", "email": "admin@example.com",
             "name": "Admin", "active": True, "share": False},
            {"id": 9, "login": "ann@example.com", "email": "ann@example.com",
             "name": "Ann A", "active": True, "share": False},
            {"id": 11, "login": "gone@example.com", "email": "gone@example.com",
             "name": "Gone", "active": False, "share": False},
        ]
        self.calls = []

    def proxy(self, url, **kw):
        return self

    def authenticate(self, db, login, pw, ctx):
        return self.uid if pw == "pw" else False

    def execute_kw(self, db, uid, pw, model, method, args, kw):
        self.calls.append((model, method, args, kw))
        if model == "ir.model.data":
            return ["res.groups", {"group_user": 1, "group_sale_salesman": 14}[args[1]]]
        if model == "auth.oauth.provider":
            return [4]
        if model == "res.partner":
            return []
        if model == "res.users" and method == "search_read":
            return [u for u in self.users if u["active"] and not u["share"]]
        if model == "res.users" and method == "search":
            email = args[0][0][2]
            active_test = kw.get("context", {}).get("active_test", True)
            return [u["id"] for u in self.users
                    if u["login"] == email and (u["active"] or not active_test)]
        if model == "res.users" and method == "write":
            for u in self.users:
                if u["id"] in args[0]:
                    u["active"] = args[1].get("active", u["active"])
            return True
        if model == "res.users" and method == "create":
            self.users.append({"id": 50, "active": True, "share": False, **args[0]})
            return 50
        raise AssertionError(f"unexpected {model}.{method}")


@contextmanager
def odoo(fake):
    with patch.object(adapters.xmlrpc.client, "ServerProxy", side_effect=fake.proxy):
        yield fake


def test_odoo_read_lists_active_internal_users(org):
    with odoo(FakeOdoo()):
        scopes, people = adapters.adapter_for(odoo_conn(org)).read()
    assert scopes == [{"slug": "", "name": ""}]
    assert set(people) == {"admin@example.com", "ann@example.com"}


def test_odoo_grant_creates_internal_user_linked_to_linkedtrust(org):
    with odoo(FakeOdoo()) as fake:
        detail = adapters.adapter_for(odoo_conn(org)).grant(
            "new@example.com", name="New Person", oauth_uid="77"
        )
    assert detail == "internal user created"
    vals = [c for c in fake.calls if c[1] == "create"][0][2][0]
    assert vals["login"] == "new@example.com"
    assert vals["groups_id"] == [(4, 1), (4, 14)]
    assert vals["oauth_provider_id"] == 4 and vals["oauth_uid"] == "77"


def test_odoo_grant_reactivates_existing_user(org):
    with odoo(FakeOdoo()) as fake:
        detail = adapters.adapter_for(odoo_conn(org)).grant("gone@example.com")
    assert detail == "internal user active"
    assert [u for u in fake.users if u["id"] == 11][0]["active"] is True
    assert not [c for c in fake.calls if c[1] == "create"]


def test_odoo_revoke_deactivates_never_deletes(org):
    with odoo(FakeOdoo()) as fake:
        assert adapters.adapter_for(odoo_conn(org)).revoke("ann@example.com") == "user deactivated"
    assert [u for u in fake.users if u["id"] == 9][0]["active"] is False
    assert not [c for c in fake.calls if c[1] == "unlink"]


def test_odoo_never_revokes_its_own_login(org):
    with odoo(FakeOdoo()):
        with pytest.raises(ToolError):
            adapters.adapter_for(odoo_conn(org)).revoke("admin@example.com")


def test_odoo_refused_login_is_a_tool_error(org):
    with odoo(FakeOdoo(uid=False)):
        with pytest.raises(ToolError):
            adapters.adapter_for(odoo_conn(org)).read()


# --- API: permissions -----------------------------------------------------------------


class FakeAdapter:
    def __init__(self, scopes=None, people=None, fail=None):
        self.scopes = scopes or [{"slug": "app", "name": "App"}]
        self.people = people or {}
        self.fail = fail
        self.calls = []

    def read(self):
        if self.fail:
            raise ToolError(self.fail)
        return self.scopes, self.people

    def grant(self, email, scope, name="", oauth_uid=""):
        self.calls.append(("grant", email, scope, name, oauth_uid))
        if self.fail:
            raise ToolError(self.fail)
        return "member, role Back"

    def revoke(self, email, scope):
        self.calls.append(("revoke", email, scope))
        if self.fail:
            raise ToolError(self.fail)
        return "membership removed"


@contextmanager
def fake_tools(mapping):
    with patch.object(services, "adapter_for", side_effect=lambda c: mapping[c.pk]):
        yield


def _matrix_url(org):
    return reverse("access-matrix", kwargs={"org_slug": org.slug})


@pytest.fixture
def people(org, user_factory, membership_factory):
    admin = user_factory(email="admin@example.com")
    granter = user_factory(email="granter@example.com")
    plain = user_factory(email="plain@example.com")
    membership_factory(org, admin, role=MembershipRole.ADMIN)
    membership_factory(org, granter, can_grant_access=True)
    membership_factory(org, plain)
    return {"admin": admin, "granter": granter, "plain": plain}


@pytest.mark.parametrize("who,status", [("admin", 200), ("granter", 200), ("plain", 403)])
def test_matrix_permission(client, org, people, who, status):
    conn = taiga_conn(org)
    client.force_login(people[who])
    with fake_tools({conn.pk: FakeAdapter()}):
        assert client.get(_matrix_url(org)).status_code == status


def test_matrix_anonymous_and_outsider(client, org, people, user_factory):
    assert client.get(_matrix_url(org)).status_code == 401
    client.force_login(user_factory(email="outside@example.com"))
    assert client.get(_matrix_url(org)).status_code == 403


def test_plain_member_cannot_grant(client, org, people):
    conn = taiga_conn(org)
    client.force_login(people["plain"])
    fake = FakeAdapter()
    with fake_tools({conn.pk: fake}):
        r = client.post(
            reverse("access-grant", kwargs={"org_slug": org.slug}),
            {"email": "x@example.com", "connection": conn.pk, "scope": "app"},
            content_type="application/json",
            **EMBED,
        )
    assert r.status_code == 403
    assert not fake.calls and not AccessChange.objects.exists()


def test_write_without_embed_header_refused(client, org, people):
    conn = taiga_conn(org)
    client.force_login(people["admin"])
    with fake_tools({conn.pk: FakeAdapter()}):
        r = client.post(
            reverse("access-grant", kwargs={"org_slug": org.slug}),
            {"email": "x@example.com", "connection": conn.pk, "scope": "app"},
            content_type="application/json",
        )
    assert r.status_code == 403
    assert not AccessChange.objects.exists()


# --- API: matrix ----------------------------------------------------------------------


def test_matrix_unions_members_and_tool_people(client, org, people):
    t, o, down = taiga_conn(org), odoo_conn(org), taiga_conn(org, enabled=True)
    off = taiga_conn(org, enabled=False)
    client.force_login(people["admin"])
    mapping = {
        t.pk: FakeAdapter(people={
            "plain@example.com": {"name": "Plain", "scopes": ["app"]},
            "contractor@example.com": {"name": "Con Tractor", "scopes": ["app"]},
        }),
        o.pk: FakeAdapter(scopes=[{"slug": "", "name": ""}], people={
            "contractor@example.com": {"name": "Con", "scopes": [""]},
        }),
        down.pk: FakeAdapter(fail="Taiga unreachable: timed out"),
        off.pk: FakeAdapter(fail="must not be read"),
    }
    with fake_tools(mapping):
        d = client.get(_matrix_url(org)).json()

    assert [x["id"] for x in d["tools"]] == sorted([t.pk, o.pk, down.pk], key=lambda pk: (
        ToolConnection.objects.get(pk=pk).display_name, pk))
    errs = {x["id"]: x.get("error") for x in d["tools"]}
    assert errs[down.pk] == "Taiga unreachable: timed out" and errs[t.pk] is None
    emails = [p["email"] for p in d["people"]]
    assert emails == ["admin@example.com", "granter@example.com", "plain@example.com",
                      "contractor@example.com"]
    by = {p["email"]: p for p in d["people"]}
    assert by["plain@example.com"]["is_member"] is True
    assert by["plain@example.com"]["access"] == {str(t.pk): ["app"]}
    con = by["contractor@example.com"]
    assert con["is_member"] is False and con["name"] == "Con Tractor"
    assert con["access"] == {str(t.pk): ["app"], str(o.pk): [""]}


# --- API: grant / revoke --------------------------------------------------------------


def _post(client, org, what, **body):
    return client.post(
        reverse(f"access-{what}", kwargs={"org_slug": org.slug}),
        body,
        content_type="application/json",
        **EMBED,
    )


def test_grant_writes_access_change(client, org, people, user_factory):
    conn = taiga_conn(org)
    user_factory(email="lt@example.com", display_name="LT Person",
                 auth_provider="linkedtrust", auth_provider_id="77")
    client.force_login(people["granter"])
    fake = FakeAdapter()
    with fake_tools({conn.pk: fake}):
        r = _post(client, org, "grant", email="LT@example.com", connection=conn.pk, scope="app")
    assert r.status_code == 200
    assert r.json()["result"] == "ok" and r.json()["detail"] == "member, role Back"
    assert fake.calls == [("grant", "lt@example.com", "app", "LT Person", "77")]
    change = AccessChange.objects.get()
    assert (change.email, change.action, change.scope, change.result, change.done_by) == (
        "lt@example.com", "grant", "app", "ok", people["granter"])


def test_revoke_failure_is_logged_and_502(client, org, people):
    conn = taiga_conn(org)
    client.force_login(people["admin"])
    with fake_tools({conn.pk: FakeAdapter(fail="Taiga answered HTTP 403")}):
        r = _post(client, org, "revoke", email="a@example.com", connection=conn.pk, scope="app")
    assert r.status_code == 502
    change = AccessChange.objects.get()
    assert (change.action, change.result, change.detail) == (
        "revoke", "failed", "Taiga answered HTTP 403")


def test_grant_rejects_bad_email_and_other_orgs_tool(client, org, people, org_factory):
    other = taiga_conn(org_factory(slug="other"))
    client.force_login(people["admin"])
    assert _post(client, org, "grant", email="nope", connection=taiga_conn(org).pk,
                 scope="app").status_code == 400
    assert _post(client, org, "grant", email="a@example.com", connection=other.pk,
                 scope="app").status_code == 400
    assert not AccessChange.objects.exists()


def test_non_member_grant_never_touches_equity(client, org, people):
    from apps.orgs.models import Membership
    from apps.pie.services import compute_pie

    conn = taiga_conn(org)
    client.force_login(people["admin"])
    before = Membership.objects.count()
    with fake_tools({conn.pk: FakeAdapter()}):
        _post(client, org, "grant", email="contractor@example.com", connection=conn.pk,
              scope="app")
    assert Membership.objects.count() == before
    assert all(s.member_label != "contractor@example.com" for s in compute_pie(org).slices)


# --- API: connections -----------------------------------------------------------------


def test_connections_admin_only_and_secret_write_only(client, org, people):
    url = reverse("access-connections", kwargs={"org_slug": org.slug})
    client.force_login(people["granter"])
    assert client.get(url).status_code == 403
    client.force_login(people["admin"])
    r = client.post(url, {"kind": "taiga", "display_name": "Boards",
                          "base_url": "https://taiga.example", "admin_login": "bot",
                          "admin_secret": "hunter2", "default_role": "Back"},
                    content_type="application/json", **EMBED)
    assert r.status_code == 201, r.content
    assert "hunter2" not in r.content.decode() and r.json()["has_secret"] is True
    pk = r.json()["id"]
    r = client.patch(reverse("access-connection", kwargs={"org_slug": org.slug, "pk": pk}),
                     {"enabled": False}, content_type="application/json", **EMBED)
    assert r.json()["enabled"] is False
    assert ToolConnection.objects.get(pk=pk).admin_secret == "hunter2"
    assert "hunter2" not in client.get(url).content.decode()


# --- pages ----------------------------------------------------------------------------


def test_access_page_for_granters_only(client, org, people):
    url = reverse("access:index", kwargs={"org_slug": org.slug})
    client.force_login(people["granter"])
    r = client.get(url)
    assert r.status_code == 200
    assert b"<govkit-access" in r.content and b'id="tools"' not in r.content
    client.force_login(people["admin"])
    assert b'id="tools"' in client.get(url).content
    client.force_login(people["plain"])
    assert client.get(url).status_code == 403


def test_admin_saves_tool_from_page_secret_not_rendered(client, org, people):
    client.force_login(people["admin"])
    r = client.post(reverse("access:save_tool", kwargs={"org_slug": org.slug}), {
        "kind": "odoo", "display_name": "CRM", "base_url": "https://crm.example",
        "database": "crm", "admin_login": "admin@example.com",
        "admin_secret_input": "hunter2", "enabled": "on",
    })
    assert r.status_code == 302
    conn = ToolConnection.objects.get()
    assert conn.admin_secret == "hunter2" and conn.org == org
    page = client.get(reverse("access:index", kwargs={"org_slug": org.slug}) + f"?tool={conn.pk}")
    assert b"hunter2" not in page.content


def test_members_page_sets_can_grant_access(client, org, people):
    from apps.orgs.models import Membership

    client.force_login(people["admin"])
    page = client.get(reverse("orgs:members", kwargs={"org_slug": org.slug}))
    assert page.status_code == 200
    assert b"<govkit-access" in page.content and b"can_grant_access" in page.content
    m = Membership.objects.get(user=people["plain"])
    client.post(
        reverse("orgs:member_update", kwargs={"org_slug": org.slug, "membership_id": m.id}),
        {"role": "member", "hourly_rate": "", "can_grant_access": "on"},
    )
    m.refresh_from_db()
    assert m.can_grant_access is True
    client.force_login(people["plain"])
    assert client.get(_matrix_url(org)).status_code == 200
