"""Mint a SHARED invite link and print it: one URL, any number of people.

The difference from `mint_invite`: that mints one person's invite, spent when
they accept. This mints a door. Everyone handed this URL walks through it and
is minted their own invite on the way, so each person still has their own
record and their own place in the pool.

    manage.py mint_invite_link <org-slug> --label 'Cohort 2 pool' \
        [--kind pool|org] [--audience founder] [--role member] \
        [--max-uses 40] [--expires-in-days 30]

Defaults open a pool door: accepting screens the person into the applicant
pool, with no org membership, no slices and no org created.
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.orgs.models import InviteAudience, InviteKind, InviteLink, MembershipRole, Org


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("org_slug")
        parser.add_argument("--label", default="", help="How you will tell this link from others.")
        parser.add_argument(
            "--kind",
            default=InviteKind.POOL,
            choices=[InviteKind.POOL, InviteKind.ORG],
            help="pool (default): the applicant pool. org: membership of this org.",
        )
        parser.add_argument(
            "--audience",
            default=InviteAudience.FOUNDER,
            choices=[c for c, _ in InviteAudience.choices],
        )
        parser.add_argument(
            "--role", default=MembershipRole.MEMBER, choices=[c for c, _ in MembershipRole.choices]
        )
        parser.add_argument("--max-uses", type=int, default=None, help="Default: no limit.")
        parser.add_argument("--expires-in-days", type=int, default=None, help="Default: no expiry.")

    def handle(self, *args, **opts):
        org = Org.objects.filter(slug=opts["org_slug"]).first()
        if org is None:
            raise CommandError(f"No org with slug '{opts['org_slug']}'.")
        if not settings.DOORWAY_BASE_URL:
            # A shared link is minted INTO an invite by the public doorway; GovKit's
            # own accept URL only knows codes that are already invites. Without a
            # doorway there is nowhere for the link to live, so say so rather than
            # printing a URL that 404s.
            raise CommandError(
                "DOORWAY_BASE_URL is not set, so there is no public door for a shared "
                "link to open. Personal invites (mint_invite) still work."
            )
        expires_at = None
        if opts["expires_in_days"]:
            expires_at = timezone.now() + timedelta(days=opts["expires_in_days"])
        link = InviteLink.objects.create(
            org=org,
            kind=opts["kind"],
            audience=opts["audience"],
            role=opts["role"],
            label=opts["label"],
            max_uses=opts["max_uses"],
            expires_at=expires_at,
        )
        # Shared links and personal invites are the same door — the doorway
        # resolves the code either way — so there is one URL shape to know.
        self.stdout.write(f"{settings.DOORWAY_BASE_URL}{link.code}/")
