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
import urllib.error
import urllib.request

from django.conf import settings
from django.core.cache import cache

from .models import Invite

logger = logging.getLogger(__name__)

_TIMEOUT = 4
_CACHE_SECONDS = 30
_CACHE_KEY = "doorway-wall-people"
# Republishing a card is a write through to LinkedTrust, not a cached read.
_CARD_TIMEOUT = 60


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
    their own words, and the booking link — and the wall fills in for everyone
    who has not signed in yet.

    The booking link is merged here rather than in the template because a dead
    link and a fresh one look the same on a page; only one of them can be the
    answer, and it is the one the mentor last set.
    """
    people, problem = _fetch_wall_people()
    people = [p for p in people if p.get("role") == "mentor"]
    accounts = _accounts_by_claim([p.get("claim_id") for p in people])
    out = []
    for p in people:
        profile = accounts.get(p.get("claim_id"))
        booking = (profile or {}).get("calendar_url") or p.get("calendar_url") or ""
        out.append({**p, "profile": profile, "calendar_url": booking})
    return out, problem


def wall_cards_by_claim(claim_ids):
    """claim_id -> what the doorway knows about them, for the ones we asked for.

    The card a person made on the wall is theirs and lives there; a page here
    that lists people should show what is on it rather than a name on its own.
    Skills are the part a venture is reading for, and `page_url` is the
    doorway's own address for the person, so nothing here has to know how the
    wall addresses anybody.

    Empty when the wall is unreachable — a list of people must still render.
    """
    ids = {c for c in claim_ids if c}
    if not ids:
        return {}
    people, _problem = _fetch_wall_people()
    return {p["claim_id"]: p for p in people if p.get("claim_id") in ids}


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
            "calendar_url": r.accepted_by.calendar_url,
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


def card_invite_for(user):
    """The accepted invite that carries this person's wall card, or None.

    An invite is the only join between an account here and a claim on the wall.
    Newest first, so someone brought in twice edits the card they have now.
    """
    return (
        Invite.objects.filter(accepted_by=user, committed_claim_id__isnull=False)
        .order_by("-accepted_at", "-id")
        .first()
    )


def put_video_on_card(invite, video_url):
    """(new_claim_id, problem) — attach an uploaded video to this person's card.

    The card is the doorway's: it holds the ledger and the LinkedTrust
    credentials, and it keeps every link already shared working across the
    replacement. All we do is say which claim and which video, then note the
    new claim id against the invite so our side stops pointing at a claim that
    no longer exists.
    """
    base = settings.DOORWAY_API_URL
    token = settings.GOVKIT_S2S_TOKEN
    # NEVER LOSE SOMEONE'S VIDEO (golda 2026-08-05). It is already uploaded by
    # the time we are called, and storage keeps no index — so a URL we do not
    # write down here is a recording nobody can ever find again. It goes on the
    # row FIRST, before anything that can fail, whether or not the card takes it.
    if video_url and invite.video_url != video_url:
        invite.video_url = video_url
        invite.save(update_fields=["video_url"])

    if not (base and token):
        return None, (
            "Cards are not wired up here: DOORWAY_API_URL or GOVKIT_S2S_TOKEN "
            "is unset in this app's environment."
        )
    payload = json.dumps({"claim_id": invite.committed_claim_id, "video_url": video_url}).encode(
        "utf-8"
    )
    req = urllib.request.Request(
        f"{base}/api/card/republish/",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_CARD_TIMEOUT) as resp:  # nosec B310
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning("card republish failed at %s: %s", base, e, exc_info=True)
        return (
            None,
            "We could not update your card just now. Your video is uploaded — try again in a minute.",
        )
    new_id = body.get("claim_id")
    if not new_id:
        return None, "The card was not updated. Try again in a minute."
    invite.committed_claim_id = new_id
    invite.save(update_fields=["committed_claim_id"])
    return new_id, ""


# --- The person's CV ------------------------------------------------------------------
#
# A CV is handed over on the way in, before this account exists, so the doorway
# holds it against the wall claim. It is not copied here: a second copy is a
# second answer to "which is their CV", and the older one wins by accident. We
# read it and write it there, and this is the only place that knows how.


def _resume_url(claim_id):
    base = settings.DOORWAY_API_URL
    token = settings.GOVKIT_S2S_TOKEN
    if not (base and token and claim_id):
        return None, None
    return f"{base}/api/wall/resume/{claim_id}/", token


def save_resume(claim_id, upload):
    """(filename, problem) — put this file on the person's wall row.

    The doorway is the one that says what a CV may be (size and format), so it
    is asked rather than second-guessed, and whatever it says comes back as the
    sentence the person reads.
    """
    url, token = _resume_url(claim_id)
    if not url:
        return "", (
            "CVs are not wired up here: DOORWAY_API_URL or GOVKIT_S2S_TOKEN "
            "is unset in this app's environment."
        )
    boundary = "----govkitcv"
    name = (getattr(upload, "name", "") or "cv")[:200]
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="resume"; filename="{name}"\r\n'
        f"Content-Type: {getattr(upload, 'content_type', '') or 'application/octet-stream'}\r\n\r\n"
    ).encode("utf-8")
    body = head + upload.read() + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_CARD_TIMEOUT) as resp:  # nosec B310
            saved = json.loads(resp.read().decode("utf-8")).get("filename", name)
        cache.delete(_CACHE_KEY)  # so the page they land back on says what is there now
        return saved, ""
    except urllib.error.HTTPError as e:
        try:
            said = json.loads(e.read().decode("utf-8")).get("error", "")
        except Exception:
            said = ""
        logger.warning("cv upload refused for claim %s: %s", claim_id, said or e)
        return "", said or "That CV could not be saved. Try again in a minute."
    except Exception as e:
        logger.warning("cv upload failed for claim %s: %s", claim_id, e, exc_info=True)
        return "", "We could not save your CV just now. Try again in a minute."


def delete_resume(claim_id):
    """Take the person's CV down. (problem,) empty when it is gone."""
    url, token = _resume_url(claim_id)
    if not url:
        return "CVs are not wired up here."
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}"}, method="DELETE"
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # nosec B310
            resp.read()
        cache.delete(_CACHE_KEY)
        return ""
    except Exception as e:
        logger.warning("cv delete failed for claim %s: %s", claim_id, e, exc_info=True)
        return "We could not remove your CV just now. Try again in a minute."


def fetch_resume(claim_id):
    """(bytes, content_type, filename) for this person's CV, or (None, "", "").

    The file is passed through rather than stored: whoever is reading it has
    already been checked by the view that called this.
    """
    url, token = _resume_url(claim_id)
    if not url:
        return None, "", ""
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=_CARD_TIMEOUT) as resp:  # nosec B310
            disposition = resp.headers.get("Content-Disposition", "")
            filename = ""
            if 'filename="' in disposition:
                filename = disposition.split('filename="', 1)[1].split('"', 1)[0]
            return (
                resp.read(),
                resp.headers.get("Content-Type", "application/octet-stream"),
                filename,
            )
    except Exception as e:
        logger.warning("cv fetch failed for claim %s: %s", claim_id, e, exc_info=True)
        return None, "", ""
