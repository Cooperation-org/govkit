"""
Vendor specifics for tool access. Nothing outside this module knows how Taiga or Odoo
store who may use them.

Each adapter is built from one ``ToolConnection`` and offers:

    read()                               -> (scopes, people)
        scopes: [{"slug", "name"}]       Taiga: one per active board; Odoo: one, blank slug
        people: {email: {"name", "scopes": [slug, ...]}}
    grant(email, scope, name, oauth_uid) -> short detail of what the tool now holds
    revoke(email, scope)                 -> short detail

Every failure is raised as ``ToolError`` with a short, factual message.

HTTP uses the standard library like ``apps.tasksources.adapters``; tests mock
``urllib.request.urlopen`` and ``xmlrpc.client.ServerProxy`` at this boundary.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import xmlrpc.client

from .models import ToolKind

TIMEOUT_SECONDS = 20

# Odoo: what an internal team user gets, by XML id (never by database id).
ODOO_GROUP_XMLIDS = ("base.group_user", "sales_team.group_sale_salesman")
# Odoo's auth_oauth provider record for LinkedTrust SSO, matched by its name.
ODOO_OAUTH_PROVIDER = "LinkedTrust"


class ToolError(Exception):
    """A tool refused, failed, or could not be reached."""


def adapter_for(connection):
    if connection.kind == ToolKind.TAIGA:
        return TaigaAccess(connection)
    if connection.kind == ToolKind.ODOO:
        return OdooAccess(connection)
    raise ToolError(f"Unknown tool kind {connection.kind!r}")


def _norm(email):
    return (email or "").strip().lower()


# ---------------------------------------------------------------------------
# Taiga
# ---------------------------------------------------------------------------


class TaigaAccess:
    """Taiga project memberships, through its REST API as the admin login."""

    def __init__(self, connection):
        self.c = connection
        self.base = connection.base_url.rstrip("/")
        self._token = None
        self._me = None

    # -- HTTP boundary --------------------------------------------------------

    def _http(self, method, path, body=None, params=None, auth=True):
        url = f"{self.base}/api/v1/{path.lstrip('/')}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        headers = {"Accept": "application/json", "x-disable-pagination": "True"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        if auth:
            headers["Authorization"] = f"Bearer {self._login()}"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(
            request, timeout=TIMEOUT_SECONDS
        ) as response:  # nosec B310 - admin-configured base_url
            payload = response.read().decode("utf-8")
        return json.loads(payload) if payload else None

    def _call(self, method, path, body=None, params=None, auth=True):
        try:
            return self._http(method, path, body=body, params=params, auth=auth)
        except urllib.error.HTTPError as exc:
            raise ToolError(f"Taiga answered HTTP {exc.code} to {method} {path}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ToolError(f"Taiga unreachable: {exc}") from exc

    def _login(self):
        if self._token is None:
            if not self.c.admin_secret:
                raise ToolError("No admin password stored for this tool.")
            me = self._call(
                "POST",
                "auth",
                body={
                    "type": "normal",
                    "username": self.c.admin_login,
                    "password": self.c.admin_secret,
                },
                auth=False,
            )
            if not me or not me.get("auth_token"):
                raise ToolError("Taiga refused the admin login.")
            self._me = me
            self._token = me["auth_token"]
        return self._token

    # -- Taiga concepts -------------------------------------------------------

    def _projects(self):
        """Active boards this login can manage.

        Archived and blocked boards are left out. A board the login only views (a
        public board of another team on the same Taiga) is left out too: Taiga would
        refuse any membership change there.
        """
        self._login()
        projects = self._call("GET", "projects", params={"member": self._me.get("id")}) or []
        return [
            p
            for p in projects
            if not p.get("blocked_code")
            and not p.get("is_archived")
            and p.get("i_am_admin", True)
        ]

    def _project(self, slug):
        for p in self._projects():
            if p.get("slug") == slug:
                return p
        raise ToolError(f"No active board {slug!r} this login manages.")

    def _memberships(self, project_id):
        rows = self._call("GET", "memberships", params={"project": project_id}) or []
        out = {}
        for m in rows:
            email = _norm(m.get("user_email") or m.get("email"))
            if email:
                out[email] = m
        return out

    def _role_id(self, project_id):
        wanted = (self.c.default_role or "").strip().lower()
        if not wanted:
            raise ToolError("No Taiga role set on this tool.")
        for role in self._call("GET", "roles", params={"project": project_id}) or []:
            if (role.get("name") or "").strip().lower() == wanted:
                return role["id"]
        raise ToolError(f"No role named {self.c.default_role!r} on this board.")

    def _is_admin_login(self, email):
        me = self._me or {}
        return email in {_norm(me.get("email")), _norm(me.get("username")), _norm(self.c.admin_login)}

    # -- interface ------------------------------------------------------------

    def read(self):
        scopes, people = [], {}
        for p in self._projects():
            scopes.append({"slug": p["slug"], "name": p.get("name") or p["slug"]})
            for email, m in self._memberships(p["id"]).items():
                person = people.setdefault(email, {"name": "", "scopes": []})
                person["name"] = person["name"] or (m.get("full_name") or "")
                person["scopes"].append(p["slug"])
        return scopes, people

    def grant(self, email, scope, name="", oauth_uid=""):
        email = _norm(email)
        project = self._project(scope)
        if email in self._memberships(project["id"]):
            return "already a member"
        role_id = self._role_id(project["id"])
        status = None
        try:
            self._http(
                "POST",
                "memberships",
                body={"project": project["id"], "role": role_id, "username": email},
            )
        except urllib.error.HTTPError as exc:
            # Taiga answers 500 when its invitation email fails even though the
            # membership was created, so the live list decides, not the status.
            status = exc.code
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ToolError(f"Taiga unreachable: {exc}") from exc
        if email in self._memberships(project["id"]):
            return f"member, role {self.c.default_role}"
        raise ToolError(f"Taiga did not add the membership (HTTP {status}).")

    def revoke(self, email, scope):
        email = _norm(email)
        project = self._project(scope)
        if self._is_admin_login(email):
            raise ToolError("That is the login GovKit uses for this tool.")
        membership = self._memberships(project["id"]).get(email)
        if membership is None:
            return "not a member"
        self._call("DELETE", f"memberships/{membership['id']}")
        if email in self._memberships(project["id"]):
            raise ToolError("Taiga still lists the membership.")
        return "membership removed"


# ---------------------------------------------------------------------------
# Odoo
# ---------------------------------------------------------------------------


class _TimeoutTransport(xmlrpc.client.Transport):
    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = TIMEOUT_SECONDS
        return conn


class _TimeoutSafeTransport(xmlrpc.client.SafeTransport):
    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = TIMEOUT_SECONDS
        return conn


class OdooAccess:
    """Odoo internal users (share=False), through XML-RPC as the admin login.

    A grant creates or re-activates the user with the internal + salesperson groups
    and, when the person signs in to GovKit through LinkedTrust, links that identity
    so "Sign in with LinkedTrust" lands on this user. A revoke deactivates the user;
    a user is never deleted.
    """

    def __init__(self, connection):
        self.c = connection
        self.base = connection.base_url.rstrip("/")
        self._uid = None
        self._models = None

    def _proxy(self, path):
        transport = (
            _TimeoutSafeTransport() if self.base.startswith("https") else _TimeoutTransport()
        )
        return xmlrpc.client.ServerProxy(
            f"{self.base}/xmlrpc/2/{path}", transport=transport, allow_none=True
        )

    def _guard(self, fn):
        try:
            return fn()
        except xmlrpc.client.Fault as exc:
            first = (exc.faultString or "").strip().splitlines()
            raise ToolError(f"Odoo refused: {first[-1] if first else exc.faultCode}") from exc
        except (xmlrpc.client.ProtocolError, OSError) as exc:
            raise ToolError(f"Odoo unreachable: {exc}") from exc

    def _ex(self, model, method, *args, **kw):
        self._connect()
        return self._guard(
            lambda: self._models.execute_kw(
                self.c.database, self._uid, self.c.admin_secret, model, method, list(args), kw
            )
        )

    def _connect(self):
        if self._uid is not None:
            return
        if not self.c.admin_secret:
            raise ToolError("No admin password stored for this tool.")
        common = self._proxy("common")
        uid = self._guard(
            lambda: common.authenticate(
                self.c.database, self.c.admin_login, self.c.admin_secret, {}
            )
        )
        if not uid:
            raise ToolError("Odoo refused the admin login.")
        self._uid = uid
        self._models = self._proxy("object")

    def _group_ids(self):
        ids = []
        for xmlid in ODOO_GROUP_XMLIDS:
            module, name = xmlid.split(".", 1)
            ids.append(self._ex("ir.model.data", "check_object_reference", module, name)[1])
        return ids

    def _users(self, email, active_only):
        domain = [("login", "=ilike", email)]
        context = {} if active_only else {"context": {"active_test": False}}
        return self._ex("res.users", "search", domain, **context)

    def read(self):
        users = self._ex(
            "res.users",
            "search_read",
            [("share", "=", False), ("active", "=", True)],
            fields=["login", "email", "name"],
        )
        people = {}
        for u in users or []:
            login, mail = _norm(u.get("login")), _norm(u.get("email"))
            email = login if "@" in login else mail
            if "@" not in email:
                continue
            people[email] = {"name": u.get("name") or "", "scopes": [""]}
        return [{"slug": "", "name": ""}], people

    def grant(self, email, scope="", name="", oauth_uid=""):
        email = _norm(email)
        vals = {
            "groups_id": [(4, gid) for gid in self._group_ids()],
            "notification_type": "inbox",
            "active": True,
        }
        if oauth_uid:
            provider = self._ex(
                "auth.oauth.provider", "search", [("name", "=", ODOO_OAUTH_PROVIDER)], limit=1
            )
            if provider:
                vals["oauth_provider_id"] = provider[0]
                vals["oauth_uid"] = str(oauth_uid)
        existing = self._users(email, active_only=False)
        if existing:
            self._ex("res.users", "write", existing, vals)
            return "internal user active"
        partner = self._ex("res.partner", "search", [("email", "=ilike", email)], limit=1)
        if partner:
            vals["partner_id"] = partner[0]
        else:
            vals["name"] = name or email
        vals["login"] = email
        vals["email"] = email
        self._ex("res.users", "create", vals)
        return "internal user created"

    def revoke(self, email, scope=""):
        email = _norm(email)
        if email == _norm(self.c.admin_login):
            raise ToolError("That is the login GovKit uses for this tool.")
        found = self._users(email, active_only=True)
        if not found:
            return "no active user"
        if self._uid in found:
            raise ToolError("That is the login GovKit uses for this tool.")
        self._ex("res.users", "write", found, {"active": False})
        return "user deactivated"
