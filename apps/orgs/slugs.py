"""Org slugs — the tenant key, and why it has to be short.

An org slug is not just a URL segment. It is the shared tenant key across
GovKit, amebo, Taiga, the team's own Odoo database (``crm-<slug>``) and its
Caddy route (``crm-<slug>.workers.vc``). earnkit's ``add-team.yml`` — the
playbook that builds all of that — asserts ``^[a-z0-9][a-z0-9-]{1,30}$`` and
stops on its first task if the slug does not match.

GovKit minted slugs up to 60 characters. A venture named "Alonovo: Value
Aligned Consumer Spending and Investing" therefore got a 53-character slug, a
GovKit org, and no stack at all: add-team failed its opening assert, nothing
downstream was built, and the team saw an org with no board, no CRM and no
agent. Nothing surfaced the failure. Minting a slug the pipeline rejects is the
bug, so the cap lives here, at the one place slugs are made.
"""

from __future__ import annotations

from django.utils.text import slugify

# add-team.yml: ^[a-z0-9][a-z0-9-]{1,30}$ — 2 to 31 characters.
MAX_SLUG_LENGTH = 31
MIN_SLUG_LENGTH = 2


def normalize_org_slug(value: str) -> str:
    """Slugify `value` into something add-team.yml accepts.

    Truncates to MAX_SLUG_LENGTH and never returns a trailing hyphen. Returns
    "" when nothing usable is left (including a result under MIN_SLUG_LENGTH),
    which is the caller's signal to use its own fallback.
    """
    slug = slugify(value or "")[:MAX_SLUG_LENGTH].rstrip("-")
    return slug if len(slug) >= MIN_SLUG_LENGTH else ""


def unique_org_slug(base: str) -> str:
    """`base`, or the first free ``base-2``, ``base-3``… that fits the cap.

    The numbered suffix eats into the base rather than pushing past the cap, so
    a collision on a long name can never produce a slug add-team will refuse.
    """
    from .models import Org

    base = normalize_org_slug(base)
    if not base:
        return ""
    slug, n = base, 2
    while Org.objects.filter(slug=slug).exists():
        suffix = f"-{n}"
        slug = base[: MAX_SLUG_LENGTH - len(suffix)].rstrip("-") + suffix
        n += 1
    return slug
