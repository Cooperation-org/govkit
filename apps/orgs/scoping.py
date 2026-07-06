"""
Org-scoping machinery shared by every domain app.

Convention (feature agents MUST follow this):

  * Every domain model that belongs to an org inherits `OrgScoped`, which supplies an
    `org` FK and an `objects` manager with `.for_org(org)`.
  * NEVER query a domain model without scoping it to an org. Use:

        DropRun.objects.for_org(request.org)

    not `DropRun.objects.all()`.
  * `request.org` and `request.membership` are set by OrgContextMiddleware for any URL
    under `/o/<org_slug>/`. See apps/orgs/middleware.py.

This keeps tenant isolation a one-liner at every call site instead of a thing to remember.
"""

from django.db import models


class OrgScopedQuerySet(models.QuerySet):
    def for_org(self, org):
        """Filter to a single org. Pass an Org instance or an org id."""
        return self.filter(org=org)


class OrgScopedManager(models.Manager.from_queryset(OrgScopedQuerySet)):
    """Default manager for org-scoped models."""


class OrgScoped(models.Model):
    """Abstract base: every domain table carries an explicit org FK."""

    org = models.ForeignKey("orgs.Org", on_delete=models.CASCADE)

    objects = OrgScopedManager()

    class Meta:
        abstract = True
