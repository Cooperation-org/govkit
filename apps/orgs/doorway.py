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
    base = settings.DOORWAY_API_URL
    token = settings.GOVKIT_S2S_TOKEN
    if not (base and token):
        return []
    cached = cache.get(_CACHE_KEY)
    if cached is not None:
        return cached
    people = []
    try:
        req = urllib.request.Request(
            f"{base}/api/wall/people/", headers={"Authorization": f"Bearer {token}"}
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # nosec B310
            people = json.loads(resp.read().decode("utf-8")).get("people", [])
    except Exception:
        logger.warning("wall people unavailable; invite form renders without the picker")
        people = []
    cache.set(_CACHE_KEY, people, _CACHE_SECONDS)
    return people


def wall_people_without_accounts():
    """Wall people nobody has signed in as yet — the ones worth inviting.

    Someone whose claim rides an accepted invite already has an account, so
    offering them again would mint a second link for a person who is in.
    """
    people = _fetch_wall_people()
    if not people:
        return []
    claim_ids = [p.get("claim_id") for p in people if p.get("claim_id")]
    signed_in = set(
        Invite.objects.filter(
            committed_claim_id__in=claim_ids, accepted_by__isnull=False
        ).values_list("committed_claim_id", flat=True)
    )
    return [p for p in people if p.get("claim_id") not in signed_in]
