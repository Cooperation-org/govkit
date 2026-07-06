"""
Create (or update) an org with one member — for local dev + verification only.

This is NOT demo-data seeding of domain records (opening balances etc. come through the
real import feature). It only bootstraps an Org + ValuationConfig + one admin Membership
so the app is navigable. All values are parameters — nothing hardcoded.

    python manage.py seed_org --slug demo --name "Demo Org" --unit points \
        --email admin@example.com --password devpass
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.orgs.models import Membership, MembershipRole, Org, ValuationConfig


class Command(BaseCommand):
    help = "Bootstrap an org + one admin membership (dev/verification use)."

    def add_arguments(self, parser):
        parser.add_argument("--slug", required=True)
        parser.add_argument("--name", required=True)
        parser.add_argument("--unit", default="points")
        parser.add_argument("--email", required=True)
        parser.add_argument("--password", default=None, help="Optional; dev login only.")

    def handle(self, *args, **opts):
        User = get_user_model()
        org, _ = Org.objects.update_or_create(
            slug=opts["slug"],
            defaults={"display_name": opts["name"], "unit_name": opts["unit"]},
        )
        ValuationConfig.objects.get_or_create(org=org)

        user, created = User.objects.get_or_create(
            email=opts["email"], defaults={"display_name": opts["email"].split("@")[0]}
        )
        if opts["password"]:
            user.set_password(opts["password"])
            user.save(update_fields=["password"])

        Membership.objects.update_or_create(
            org=org, user=user, defaults={"role": MembershipRole.ADMIN}
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Org '{org.slug}' ready with admin member {user.email} "
                f"(user {'created' if created else 'existing'})."
            )
        )
