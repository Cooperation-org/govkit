"""
Auto-drop: sync every org's task sources, then open a drop run where work is waiting.

    python manage.py autodrop

Run this on a schedule (deploy/govkit-autodrop.timer). It closes the gap between "I
finished my task" and "my points show on the pie": a member's approved-in-tracker work
gathers into an OPEN run by itself, showing up as pending on their standing page.
Approval stays human — a steward still reviews and approves the run before anything
becomes issued equity. This command never approves.

Idempotent and safe to re-run: sync upserts tracked tasks, a task already gathered
into a line is never gathered again, and an org with nothing new simply opens no run.
"""

from django.core.management.base import BaseCommand

from apps.orgs.models import Org
from apps.tasksources.models import TaskSourceConfig
from apps.tasksources.services import sync_org

from ...services import NoEligibleTasks, open_run


class Command(BaseCommand):
    help = "Sync task sources for all orgs and open a drop run wherever work is waiting."

    def handle(self, *args, **opts):
        org_ids = TaskSourceConfig.objects.values_list("org_id", flat=True).distinct()
        for org in Org.objects.filter(id__in=org_ids):
            try:
                results = sync_org(org)
            except Exception as exc:  # one org's broken tracker must not stop the rest
                self.stderr.write(f"{org.slug}: sync failed: {exc}")
                continue
            fetched = sum(r.fetched for r in results)
            try:
                run = open_run(org)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{org.slug}: synced {fetched}, opened run #{run.pk} "
                        f"({run.lines.count()} lines) — awaiting steward approval"
                    )
                )
            except NoEligibleTasks:
                self.stdout.write(f"{org.slug}: synced {fetched}, nothing new to drop")
