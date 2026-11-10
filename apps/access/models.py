"""
Who may use the team's tools (Taiga boards, the Odoo CRM).

The tools are the record of who has access. GovKit keeps only:

* ``ToolConnection`` — how to reach one tool as its admin (secret encrypted at rest,
  never shown back), and which Taiga role a grant gives.
* ``AccessChange`` — an append-only log of each grant / revoke made from here.

Who currently has access is read live from each tool on every request
(``services.access_matrix``); it is never copied into a table here.

None of this touches the pie or equity: a person given access by email need not be a
GovKit member at all.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.orgs.scoping import OrgScoped
from apps.tasksources.fields import EncryptedTextField


class ToolKind(models.TextChoices):
    TAIGA = "taiga", "Taiga"
    ODOO = "odoo", "Odoo"


class ToolConnection(OrgScoped):
    kind = models.CharField(max_length=16, choices=ToolKind.choices)
    display_name = models.CharField(max_length=100)
    base_url = models.URLField(max_length=1000)
    # Odoo's XML-RPC needs the database name; Taiga has none.
    database = models.CharField(max_length=100, blank=True)
    admin_login = models.CharField(max_length=255)
    admin_secret = EncryptedTextField(blank=True)
    # The Taiga role a grant gives, by name (e.g. "Back"). Odoo ignores it.
    default_role = models.CharField(max_length=100, blank=True)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["org", "display_name", "id"]

    def __str__(self):
        return f"{self.display_name} ({self.get_kind_display()}, {self.org.slug})"

    def clean(self):
        if self.kind == ToolKind.ODOO and not self.database:
            raise ValidationError({"database": "Odoo needs its database name."})
        if self.kind == ToolKind.TAIGA and not self.default_role:
            raise ValidationError({"default_role": "Name the Taiga role to grant."})


class AccessAction(models.TextChoices):
    GRANT = "grant", "Grant"
    REVOKE = "revoke", "Revoke"


class AccessResult(models.TextChoices):
    OK = "ok", "OK"
    FAILED = "failed", "Failed"


class AccessChange(OrgScoped):
    """One grant or revoke made from GovKit. Written once, never edited."""

    connection = models.ForeignKey(
        ToolConnection, on_delete=models.PROTECT, related_name="changes"
    )
    # Taiga project slug; blank for a tool with no sub-scopes (Odoo).
    scope = models.CharField(max_length=255, blank=True)
    email = models.EmailField()
    action = models.CharField(max_length=8, choices=AccessAction.choices)
    done_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="access_changes",
    )
    done_at = models.DateTimeField(auto_now_add=True)
    result = models.CharField(max_length=8, choices=AccessResult.choices)
    detail = models.TextField(blank=True)

    class Meta:
        ordering = ["-done_at", "-id"]

    def __str__(self):
        where = f"{self.connection.display_name} {self.scope}".strip()
        return f"{self.action} {self.email} @ {where}: {self.result}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("AccessChange is append-only.")
        super().save(*args, **kwargs)
