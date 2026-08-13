"""
The attention feed — everything on a dash's right-hand rail that wants a human.

One generic item shape so new kinds slot in without touching the embed contract:

    {kind, id, title, detail, email, since, done, url, org_slug, respond_url}

  kind      "venture_interest" today; "pool_pending" on the accelerator's rail;
            future kinds (drops to approve, votes to cast, ...) reuse the shape.
  done      answered/handled — the client renders it dimmed, at the bottom.
  url       a link out when the action lives elsewhere (e.g. the doorway's
            approval queue).
  url_label what that link says, when "review" is the wrong word for it (the
            person's own rail links them to their new team's dash).
  respond_url  the mark-answered endpoint for this row, relative to the GovKit
            root. Present means the client draws the button; that is the whole
            contract, so a new kind gets one by filling the field, not by
            teaching the embed its name.
  accept_url   the endpoint that grants what the row asks for — for a hand-raise,
            the membership. Same contract as respond_url: present means the
            client draws the button. Absent when there is nothing to grant, when
            the viewer cannot grant it, or when it is already granted, so the
            button is never shown to someone it would fail for.
  accept_label  what that button says, in the words of what it does.

Facts stay in their homes: interest rows here in commons, pending walk-ups in
the workersvc doorway's ledger (read over its loopback S2S API, never copied).
"""

import json
import logging
import urllib.request

from django.conf import settings
from django.core.cache import cache

from .models import SponsorPledge, VentureInterest

logger = logging.getLogger(__name__)

_DOORWAY_TIMEOUT = 4
_DOORWAY_CACHE_SECONDS = 30


def _already_in(i):
    from apps.orgs.models import Membership

    return Membership.objects.filter(org_id=i.org_id, user_id=i.user_id).exists()


def _interest_item(i, with_org_name, can_admit=False):
    who = i.user.get_full_name() or i.user.email
    title = f"{who} wants to join {i.org.display_name}" if with_org_name else f"{who} wants to join"
    item = {
        "kind": "venture_interest",
        "id": i.id,
        "title": title,
        "detail": i.note,
        "email": i.user.email,
        "since": i.created_at.isoformat(),
        "done": i.responded_at is not None,
        "url": "",
        "org_slug": i.org.slug,
        "respond_url": f"/api/v1/commons/orgs/{i.org.slug}/interest/{i.id}/respond/",
    }
    if can_admit and not _already_in(i):
        item["accept_url"] = f"/api/v1/commons/orgs/{i.org.slug}/interest/{i.id}/accept/"
        item["accept_label"] = "Add to team"
    return item


def _can_admit(user, org):
    """Whether this viewer can put someone into this org."""
    from apps.commons.api import _is_org_admin

    return _is_org_admin(user, org)


def org_interest_items(org, viewer=None):
    """This venture's waiting list, unanswered first (model ordering).

    `viewer` decides whether the rows carry the add-to-team action: it is the
    admin's to do, and a button nobody can press does not belong on the page.
    """
    rows = VentureInterest.objects.filter(org=org).select_related("org", "user")
    admit = _can_admit(viewer, org) if viewer is not None else False
    return [_interest_item(i, with_org_name=False, can_admit=admit) for i in rows]


def all_open_interest_items(viewer=None):
    """Every unanswered hand-raise across ventures — the accelerator's read.

    Only ever built for an accelerator admin, who runs the cohort; the admit
    check still runs per-org so it is the org's own rule that decides.
    """
    rows = VentureInterest.objects.filter(responded_at__isnull=True).select_related("org", "user")
    return [
        _interest_item(
            i,
            with_org_name=True,
            can_admit=_can_admit(viewer, i.org) if viewer is not None else False,
        )
        for i in rows
    ]


def sponsor_pledge_items(org):
    """Sponsorship offered to this org, unanswered first (model ordering).

    Answered ones stay on the rail (dimmed) rather than disappearing: there is
    no other place in the product yet where the team can see who offered, and a
    pledge that vanishes on first reply is a pledge nobody follows up.
    """
    rows = SponsorPledge.objects.filter(org=org).select_related("org")
    return [
        {
            "kind": "sponsor_pledge",
            "id": p.id,
            "title": f"{p.who} offered to sponsor — {p.summary}",
            "detail": " ".join(x for x in (p.offer, p.note) if x),
            "email": p.email,
            "since": p.created_at.isoformat(),
            "done": p.responded_at is not None,
            "url": "",
            "org_slug": p.org.slug,
            "respond_url": f"/api/v1/commons/sponsor-pledges/{p.id}/respond/",
        }
        for p in rows
    ]


def invite_accepted_items():
    """Recent invite accepts — awareness for the accelerator rail. Direct
    invites (no commit ceremony, no wall card) would otherwise be invisible
    the moment they happen; this is where they show. Last 7 days; rows
    accepted before accepted_at existed stay silent."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.orgs.models import Invite, InviteStatus

    rows = (
        Invite.objects.filter(
            status=InviteStatus.ACCEPTED,
            accepted_at__gte=timezone.now() - timedelta(days=30),
        )
        .select_related("org", "accepted_by")
        .order_by("-accepted_at")[:10]
    )
    items = []
    for inv in rows:
        who = (
            (inv.accepted_by and (inv.accepted_by.get_full_name() or inv.accepted_by.email))
            or inv.name
            or "Someone"
        )
        items.append(
            {
                "kind": "invite_accepted",
                "id": inv.id,
                "title": f"{who} accepted a {inv.get_audience_display().lower()} invite"
                + (f" to {inv.org.display_name}" if inv.org else ""),
                "detail": inv.get_kind_display(),
                "email": "",
                "since": inv.accepted_at.isoformat(),
                # Awareness only — the join already happened.
                "done": True,
                "url": "",
                "org_slug": "",
            }
        )
    return items


def doorway_items(for_venture=None):
    """The doorway's side of the rail: walk-ups pending approval (actionable)
    and recent approved joins (awareness — invited people are auto-approved,
    so this is the only feed that would ever mention them).

    A walk-up carries the team whose join page they came from. Pass that slug
    as `for_venture` for a team's own rail and only their people come back;
    pass nothing for the accelerator's rail, which sees everyone and gets the
    team named in the title. A hand raised at a team has to reach that team —
    it used to land here unattributed and they never heard about it.

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
        return _for_venture(cached, for_venture)
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
                    "title": f"{r.get('person_name') or 'Someone'} is waiting at the door",
                    "detail": "",
                    "email": "",
                    "since": r.get("created_at", ""),
                    "done": False,
                    # The approval action lives in the doorway's own admin.
                    "url": r.get("approve_url", ""),
                    "org_slug": "",
                    "role": r.get("role", ""),
                    "venture_slug": r.get("venture_slug", ""),
                    "venture_name": r.get("venture_name", ""),
                }
            )
        for r in payload.get("recent", []):
            detail = (
                f"invited by {r['inviter']}"
                if r.get("inviter")
                else ("invited" if r.get("invited") else "walk-up, approved")
            )
            items.append(
                {
                    "kind": "wall_joined",
                    "id": r.get("id"),
                    "title": f"{r.get('person_name') or 'Someone'} joined the wall",
                    "detail": detail,
                    "email": "",
                    "since": r.get("created_at", ""),
                    # Nothing to do — renders dimmed, below the actionable rows.
                    "done": True,
                    "url": "",
                    "org_slug": "",
                    "role": r.get("role", ""),
                    "venture_slug": r.get("venture_slug", ""),
                    "venture_name": r.get("venture_name", ""),
                }
            )
    except Exception as e:
        logger.warning("attention: doorway unreachable: %s", e)
    cache.set("doorway-attention", items, _DOORWAY_CACHE_SECONDS)
    return _for_venture(items, for_venture)


def _for_venture(items, slug):
    """One cached read of the doorway, cut two ways.

    A team's rail gets only the people who came for that team, said plainly.
    The accelerator's rail gets everyone, with the team named — otherwise a
    queue of walk-ups gives no way to tell who is waiting on whom.
    """
    out = []
    for item in items:
        if slug and item.get("venture_slug") != slug:
            continue
        name = item.get("venture_name") or item.get("venture_slug")
        title = item["title"]
        if name and not slug:
            title += f" for {name}"
        if item.get("role"):
            title += f" ({item['role']})"
        out.append({**item, "title": title})
    return out


def my_news_items(user):
    """The other side of the rail: what the ventures did about THIS person.

    A worker raises a hand and then hears nothing — the row they made lives on
    the team's rail, not theirs, and the mail that would have told them is off
    on this install (mail.py). This is where they find out, in the same item
    shape every rail uses, so the embed renders it with no new contract.

    Same facts, no new home: interest rows and memberships. Being let in is a
    membership, so a venture that admitted someone is reported from the
    membership and its interest row is dropped — one event, one line.

    `url` carries a link the person can follow (their new team's dash);
    `url_label` names it, since "review" is the venture's word, not theirs.
    `done` means nothing has happened yet: a hand still waiting renders dimmed
    under the answers, which is the news they came for.
    """
    from apps.orgs.invites import cohort_front_door_url
    from apps.orgs.models import Membership

    items = []
    memberships = list(Membership.objects.filter(user=user).select_related("org"))
    for m in memberships:
        items.append(
            {
                "kind": "you_joined",
                "id": m.id,
                "title": f"You are in — {m.org.display_name}",
                "detail": "",
                "email": "",
                "since": m.created_at.isoformat(),
                "done": False,
                "url": cohort_front_door_url(m.org) or "",
                "url_label": "your team's dash",
                "org_slug": m.org.slug,
            }
        )
    joined = {m.org_id for m in memberships}
    for i in VentureInterest.objects.filter(user=user).select_related("org"):
        if i.org_id in joined:
            continue
        answered = i.responded_at is not None
        items.append(
            {
                "kind": "interest_answered" if answered else "interest_waiting",
                "id": i.id,
                "title": (
                    f"{i.org.display_name} answered you"
                    if answered
                    else f"Waiting on {i.org.display_name}"
                ),
                "detail": "" if answered else i.note,
                "email": "",
                "since": (i.responded_at if answered else i.created_at).isoformat(),
                "done": not answered,
                "url": "",
                "url_label": "",
                "org_slug": i.org.slug,
            }
        )
    # Newest first, and anything still waiting after everything that happened.
    items.sort(key=lambda x: x["since"], reverse=True)
    items.sort(key=lambda x: x["done"])
    return items
