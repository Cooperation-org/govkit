"""The wall, read from the doorway, for the invite form's picker.

People approved on the workers.vc wall attested before they had an account
anywhere: their claim exists and nothing else does. The members page lists them
so an admin mints an invite against the claim they already made instead of
retyping their name and pasting a claim id.

The ledger stays in the doorway and nothing is copied. What IS ours is who among
them has an account: an invite carrying their claim id that someone accepted.
That filter happens here, against our own rows, so the doorway never has to
guess at our side of it.

Loopback S2S, same shared bearer the doorway uses to call us. Cached briefly;
ANY failure returns [] so the form simply renders without the picker.
"""

import json
import logging
import urllib.request

from django.conf import settings
from django.core.cache import cache

from .models import Invite

logger = logging.getLogger(__name__)

_TIMEOUT = 4
_CACHE_SECONDS = 30
_CACHE_KEY = "doorway-wall-people"


def _fetch_wall_people():
    """(people, problem). `problem` is a sentence to show, or "" when fine.

    A picker that silently renders nothing is indistinguishable from a picker
    that was never deployed, which is exactly how an afternoon gets lost. So
    every way this can come back empty says which way it was.
    """
    base = settings.DOORWAY_API_URL
    token = settings.GOVKIT_S2S_TOKEN
    if not (base and token):
        return [], (
            "The wall is not wired up here: DOORWAY_API_URL or GOVKIT_S2S_TOKEN "
            "is unset in this app's environment."
        )
    cached = cache.get(_CACHE_KEY)
    if cached is not None:
        return cached
    try:
        req = urllib.request.Request(
            f"{base}/api/wall/people/", headers={"Authorization": f"Bearer {token}"}
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # nosec B310
            result = json.loads(resp.read().decode("utf-8")).get("people", []), ""
    except Exception as e:
        logger.warning("wall people unreachable at %s: %s", base, e, exc_info=True)
        result = [], f"Could not read the wall at {base}: {e}"
    cache.set(_CACHE_KEY, result, _CACHE_SECONDS)
    return result


def mentors():
    """(mentors, problem) — the cohort's mentors and the calendars they shared.

    A mentor gave a booking link so teams could actually book them. It is kept
    off their public wall card on purpose: they shared it with the cohort, not
    with the open internet. Callers must gate it; this only fetches.

    A mentor who has since signed in also has a profile here, which they can
    edit and their wall claim they cannot: a claim is signed and immutable. So
    the profile wins wherever it has something — photo, the name they go by,
    and their own words — and the wall fills in for everyone who has not
    signed in yet.
    """
    people, problem = _fetch_wall_people()
    people = [p for p in people if p.get("role") == "mentor"]
    accounts = _accounts_by_claim([p.get("claim_id") for p in people])
    return [{**p, "profile": accounts.get(p.get("claim_id"))} for p in people], problem


def _accounts_by_claim(claim_ids):
    """claim_id -> that person's profile here, for the ones who have signed in.

    The link is the invite that carried their claim and was accepted; there is
    no other join between a wall claim and an account.
    """
    ids = [c for c in claim_ids if c]
    if not ids:
        return {}
    rows = Invite.objects.filter(
        committed_claim_id__in=ids, accepted_by__isnull=False
    ).select_related("accepted_by")
    return {
        r.committed_claim_id: {
            "display_name": r.accepted_by.get_full_name(),
            "avatar_url": r.accepted_by.avatar_url,
            "bio": r.accepted_by.bio,
        }
        for r in rows
    }


def wall_people_without_accounts():
    """(people, problem) for the invite form's picker.

    People here have a claim and no account. Someone whose claim rides an
    accepted invite already signed in, so offering them again would mint a
    second link for a person who is in.
    """
    people, problem = _fetch_wall_people()
    if not people:
        return [], problem or "Everyone on the wall already has an account."
    claim_ids = [p.get("claim_id") for p in people if p.get("claim_id")]
    signed_in = set(
        Invite.objects.filter(
            committed_claim_id__in=claim_ids, accepted_by__isnull=False
        ).values_list("committed_claim_id", flat=True)
    )
    left = [p for p in people if p.get("claim_id") not in signed_in]
    return left, "" if left else "Everyone on the wall already has an account."
