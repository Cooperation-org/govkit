"""
The CRM adapter — the one file in comms that talks to Odoo.

Each team has its own CRM: one Odoo database per team, named after the team's
slug, and Odoo's dbfilter there makes the hostname the database name
(`crm-vc.workers.vc` serves `crm-vc`, one to one). So the address and the
database are worked out from the org whose comms this is, not fixed in config —
which is also what makes a venture importing its own supporters the same code
path as the accelerator importing its own. The accelerator's CRM is one of these
teams' CRMs; it is not the LinkedTrust CRM on the dev VM, which shares no
databases with it.

Contacts live in the CRM and nowhere else. Comms does not copy them: an import
brings back a handle (`res.partner` id), the address to send to, and the name to
say, and re-importing refreshes those from the CRM again. Editing who a person
is still happens in the CRM.

Two things are honoured on the way in, because getting them wrong sends mail to
someone who said no:

  * `is_blacklisted` — Odoo 17's own opt-out, backed by `mail.blacklist` and
    keyed by email. There is no `opt_out` field on `res.partner`; a plan that
    names one is out of date.
  * no address, no row. A contact with no email is not a recipient.

The selector is a contact tag (`res.partner.category`), not a role — that is the
point of this list. Tags are read back from the CRM so a person picks a real one
from a real count instead of typing a name that has to match.

Off unless `COMMS_CRM_URL_PATTERN` and `COMMS_CRM_KEY` are set. Nothing renders
when it is off; an Import button that cannot import is worse than no button.
"""

from __future__ import annotations

import logging
import xmlrpc.client

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

PAGE = 500
# Counting every tag is one round trip per tag, so the picker's list is held for
# a few minutes. The import itself always reads the CRM fresh.
TAGS_CACHE_KEY = "comms:crm:tags"
TAGS_CACHE_SECONDS = 300
# What we read back per contact. Nothing else is copied.
FIELDS = ["id", "name", "email", "is_blacklisted"]


def available() -> bool:
    return bool(
        getattr(settings, "COMMS_CRM_URL_PATTERN", "")
        and getattr(settings, "COMMS_CRM_KEY", "")
    )


def where(org_slug: str) -> tuple[str, str]:
    """(base url, database) for one org's own CRM."""
    url = settings.COMMS_CRM_URL_PATTERN.format(slug=org_slug).rstrip("/")
    return url, settings.COMMS_CRM_DB_PATTERN.format(slug=org_slug)


def _connect(org_slug: str):
    """(uid, models proxy, db, key). Raises on anything that is not a connection."""
    url, db = where(org_slug)
    user = settings.COMMS_CRM_USER
    key = settings.COMMS_CRM_KEY
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, user, key, {})
    if not uid:
        raise PermissionError("the CRM did not accept those credentials")
    return uid, xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object"), db, key


def tags(org_slug: str) -> tuple[list[dict], str]:
    """([{id, name, count}], problem) — the CRM's own contact tags.

    `count` is how many of that tag's contacts could actually be mailed, so the
    number on screen is the number that would be imported.
    """
    if not available():
        return [], ""
    key_ = f"{TAGS_CACHE_KEY}:{org_slug}"
    cached = cache.get(key_)
    if cached is not None:
        return cached
    try:
        uid, models, db, key = _connect(org_slug)
        rows = models.execute_kw(
            db, uid, key, "res.partner.category", "search_read", [[]],
            {"fields": ["id", "name"], "order": "name"},
        )
        out = []
        for row in rows:
            count = models.execute_kw(
                db, uid, key, "res.partner", "search_count", [_domain(row["id"])]
            )
            if count:
                out.append({"id": row["id"], "name": row["name"], "count": count})
        result = (out, "")
    except Exception as exc:
        logger.warning("comms: could not read CRM tags: %s", exc, exc_info=True)
        result = ([], f"Could not read the CRM: {exc}")
    cache.set(key_, result, TAGS_CACHE_SECONDS)
    return result


def people(org_slug: str, tag_id: int) -> tuple[list[dict], str]:
    """([{external_id, email, name}], problem) — everyone under one tag."""
    if not available():
        return [], ""
    try:
        uid, models, db, key = _connect(org_slug)
        out, offset = [], 0
        while True:
            rows = models.execute_kw(
                db, uid, key, "res.partner", "search_read", [_domain(tag_id)],
                {"fields": FIELDS, "limit": PAGE, "offset": offset, "order": "id"},
            )
            if not rows:
                break
            out += [
                {
                    "external_id": str(row["id"]),
                    "email": (row.get("email") or "").strip().lower(),
                    "name": (row.get("name") or "").strip(),
                }
                for row in rows
                if row.get("email") and not row.get("is_blacklisted")
            ]
            offset += PAGE
        return out, ""
    except Exception as exc:
        logger.warning("comms: CRM import failed: %s", exc, exc_info=True)
        return [], f"Could not read the CRM: {exc}"


def _domain(tag_id: int) -> list:
    """Tagged, has an address, and has not asked us to stop."""
    return [
        ["category_id", "in", [tag_id]],
        ["email", "!=", False],
        ["is_blacklisted", "=", False],
    ]
