"""
Task-source config + fetched tasks.

A TaskSourceConfig tells the (stubbed) adapter how to reach a tracker (Taiga first) and
how to read value/hours off tasks. TrackedTask is the local mirror of a fetched task; its
(org, source, external_id) uniqueness replaces the legacy dedup-by-task-array trick.
"""

from django.db import models

from apps.orgs.scoping import OrgScoped

from .fields import EncryptedTextField


def default_done_statuses():
    return ["done", "archived", "historical"]


class AdapterType(models.TextChoices):
    TAIGA = "taiga", "Taiga"


class TaskSourceConfig(OrgScoped):
    adapter_type = models.CharField(
        max_length=32, choices=AdapterType.choices, default=AdapterType.TAIGA
    )
    base_url = models.URLField(help_text="Tracker REST API base URL.")
    api_token = EncryptedTextField(blank=True, help_text="Encrypted at rest (Fernet).")
    project_selector = models.CharField(
        max_length=255,
        blank=True,
        help_text="Which project(s) to pull, e.g. a Taiga project slug or id.",
    )

    # Field mapping — which task metadata carries value/hours.
    value_tag_pattern = models.CharField(
        max_length=128,
        default=r"(\d+)\s*",
        help_text=(
            "Regex (case-insensitive) matched against task tags in direct_value mode; "
            r"group 1 is the numeric value. e.g. r'(\d+)\s*cook'."
        ),
    )
    hours_field = models.CharField(
        max_length=128,
        blank=True,
        help_text="Name of the custom attribute / field carrying hours in hours_rate mode.",
    )
    cash_field = models.CharField(
        max_length=128,
        blank=True,
        help_text="Name of the custom attribute / field carrying cash attached to a task.",
    )
    done_statuses = models.JSONField(
        default=default_done_statuses,
        help_text="Status slugs that count as done/eligible.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "task source config"
        verbose_name_plural = "task source configs"

    def __str__(self):
        return f"{self.get_adapter_type_display()} source ({self.org.slug})"


class TrackedTask(OrgScoped):
    source = models.ForeignKey(TaskSourceConfig, on_delete=models.CASCADE, related_name="tasks")
    external_id = models.CharField(max_length=128)
    external_url = models.URLField(blank=True)
    subject = models.CharField(max_length=500, blank=True)
    assignee = models.ForeignKey(
        "orgs.Membership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tracked_tasks",
    )

    # Valuation inputs (any may be null until reviewed).
    claimed_value = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    hours = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    cash = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)

    status = models.CharField(max_length=64, blank=True)
    fetched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["org", "source", "external_id"],
                name="uniq_task_org_source_external",
            ),
        ]
        ordering = ["-fetched_at", "external_id"]

    def __str__(self):
        return f"{self.external_id}: {self.subject[:40]}"

    @property
    def is_missing_value(self):
        """True when neither a direct value nor hours has been captured yet.

        Drives the steward review queue (tasks needing a value before they can drop).
        """
        return self.claimed_value is None and self.hours is None
