"""
Sync an org's task sources from their tracker (Taiga) over REST.

    python manage.py sync_tasksource <org_slug>

Idempotent: re-running updates existing TrackedTask rows (keyed on
``(org, source, external_id)``) instead of duplicating. A scheduler/cron should call
``apps.tasksources.services.refresh_all`` (all orgs) rather than shelling out per org.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.orgs.models import Org
from apps.tasksources.services import sync_org


class Command(BaseCommand):
    help = "Fetch done/archived/historical tasks for an org and upsert TrackedTask rows."

    def add_arguments(self, parser):
        parser.add_argument("org_slug", help="Slug of the org whose sources to sync.")

    def handle(self, *args, **opts):
        slug = opts["org_slug"]
        try:
            org = Org.objects.get(slug=slug)
        except Org.DoesNotExist as exc:
            raise CommandError(f"No org with slug '{slug}'.") from exc

        results = sync_org(org)
        if not results:
            self.stdout.write(self.style.WARNING(f"Org '{slug}' has no task sources configured."))
            return

        for r in results:
            self.stdout.write(
                self.style.SUCCESS(
                    f"source {r.source_id}: fetched {r.fetched}, "
                    f"created {r.created}, updated {r.updated}, unassigned {r.unassigned}"
                )
            )
            for err in r.errors:
                self.stdout.write(self.style.ERROR(f"  error: {err}"))
