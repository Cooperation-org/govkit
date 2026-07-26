"""
The attention feed — everything on a dash's right-hand rail that wants a human.

One generic item shape so new kinds slot in without touching the embed contract:

    {kind, id, title, detail, email, since, done, url, org_slug}

  kind      "venture_interest" today; "pool_pending" on the accelerator's rail;
            future kinds (drops to approve, votes to cast, ...) reuse the shape.
  done      answered/handled — the client renders it dimmed, at the bottom.
  url       a link out when the action lives elsewhere (e.g. the doorway's
            approval queue); venture_interest instead carries org_slug + id so
            the client can POST the mark-answered endpoint.

Facts stay in their homes: interest rows here in commons, pending walk-ups in
the workersvc doorway's ledger (read over its loopback S2S API, never copied).
"""

import json
import logging
import urllib.request

from django.conf import settings
from django.core.cache import cache

from .models import VentureInterest

logger = logging.getLogger(__name__)

_DOORWAY_TIMEOUT = 4
_DOORWAY_CACHE_SECONDS = 30


def _interest_item(i, with_org_name):
    who = i.user.get_full_name() or i.user.email
    title = f"{who} wants to join {i.org.display_name}" if with_org_name else f"{who} wants to join"
    return {
        "kind": "venture_interest",
        "id": i.id,
        "title": title,
        "detail": i.note,
        "email": i.user.email,
        "since": i.created_at.isoformat(),
        "done": i.responded_at is not None,
        "url": "",
        "org_slug": i.org.slug,
    }


def org_interest_items(org):
    """This venture's waiting list, unanswered first (model ordering)."""
    rows = VentureInterest.objects.filter(org=org).select_related("org", "user")
    return [_interest_item(i, with_org_name=False) for i in rows]


def all_open_interest_items():
    """Every unanswered hand-raise across ventures — the accelerator's read."""
    rows = VentureInterest.objects.filter(responded_at__isnull=True).select_related(
        "org", "user"
    )
    return [_interest_item(i, with_org_name=True) for i in rows]


def doorway_items():
    """The doorway's side of the rail: walk-ups pending approval (actionable)
    and recent approved joins (awareness — invited people are auto-approved,
    so this is the only feed that would ever mention them).

    Loopback S2S (same VM, same shared bearer the doorway already uses to call
    us). Cached briefly; ANY failure returns [] — the rail just shows less,
    never an error. Empty DOORWAY_API_URL disables this source entirely.
    """
    base = settings.DOORWAY_API_URL
    token = settings.GOVKIT_S2S_TOKEN
    if not (base and token):
        return []
    cached = cache.get("doorway-attention")
    if cached is not None:
        return cached
    items = []
    try:
        req = urllib.request.Request(
            f"{base}/api/wall/pending/", headers={"Authorization": f"Bearer {token}"}
        )
        with urllib.request.urlopen(req, timeout=_DOORWAY_TIMEOUT) as resp:  # nosec B310
            payload = json.loads(resp.read().decode("utf-8"))
        for r in payload.get("pending", []):
            items.append(
                {
                    "kind": "pool_pending",
                    "id": r.get("id"),
                    "title": f"{r.get('person_name') or 'Someone'} is waiting at the door"
                    + (f" ({r['role']})" if r.get("role") else ""),
                    "detail": "",
                    "email": "",
                    "since": r.get("created_at", ""),
                    "done": False,
                    # The approval action lives in the doorway's own admin.
                    "url": r.get("approve_url", ""),
                    "org_slug": "",
                }
            )
        for r in payload.get("recent", []):
            who = r.get("person_name") or "Someone"
            role = f" ({r['role']})" if r.get("role") else ""
            detail = f"invited by {r['inviter']}" if r.get("inviter") else (
                "invited" if r.get("invited") else "walk-up, approved"
            )
            items.append(
                {
                    "kind": "wall_joined",
                    "id": r.get("id"),
                    "title": f"{who} joined the wall{role}",
                    "detail": detail,
                    "email": "",
                    "since": r.get("created_at", ""),
                    # Nothing to do — renders dimmed, below the actionable rows.
                    "done": True,
                    "url": "",
                    "org_slug": "",
                }
            )
    except Exception as e:
        logger.warning("attention: doorway unreachable: %s", e)
    cache.set("doorway-attention", items, _DOORWAY_CACHE_SECONDS)
    return items
