"""Mint a magic-link invite from the command line and print the share link.

The operator-side twin of the Members-page invite form, for provisioning runs
(earnkit add-team can hand a new team's founder their admin invite in the same
command). Same rules as the UI: single-use, expiring, doorway routing whenever
DOORWAY_BASE_URL is configured.

    manage.py mint_invite <org-slug> --name 'Jefferson Richards' \
        --role admin --audience founder [--email j@example.com] [--link URL]
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from apps.orgs.models import Invite, InviteAudience, InviteKind, MembershipRole, Org


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("org_slug")
        parser.add_argument("--name", required=True)
        parser.add_argument("--email", default="")
        parser.add_argument("--link", default="")
        parser.add_argument(
            "--role", default=MembershipRole.MEMBER, choices=[c for c, _ in MembershipRole.choices]
        )
        parser.add_argument(
            "--audience",
            default=InviteAudience.SUPPORTER,
            choices=[c for c, _ in InviteAudience.choices],
        )
        parser.add_argument("--venture-name", default="")
        parser.add_argument("--venture-url", default="")
        parser.add_argument(
            "--pool",
            action="store_true",
            help="Pool invite: accepting screens them into the applicant pool — "
            "no org membership, no slices, no org created.",
        )

    def handle(self, *args, **opts):
        org = Org.objects.filter(slug=opts["org_slug"]).first()
        if org is None:
            raise CommandError(f"No org with slug '{opts['org_slug']}'.")
        if opts["pool"] and opts["venture_name"]:
            raise CommandError(
                "--pool and --venture-name are contradictory: a pool invite joins "
                "no org and creates none."
            )
        invite = Invite.objects.create(
            org=org,
            role=opts["role"],
            audience=opts["audience"],
            kind=InviteKind.POOL if opts["pool"] else InviteKind.ORG,
            name=opts["name"],
            email=opts["email"],
            link=opts["link"],
            venture_name=opts["venture_name"],
            venture_url=opts["venture_url"],
            doorway=bool(settings.DOORWAY_BASE_URL),
        )
        if invite.doorway:
            share = f"{settings.DOORWAY_BASE_URL}{invite.code}/"
        else:
            base = (getattr(settings, "PUBLIC_BASE_URL", "") or "").rstrip("/")
            share = base + reverse("orgs:accept_invite", kwargs={"code": invite.code})
        self.stdout.write(share)
