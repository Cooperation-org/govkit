# apps.orgs — tenancy + org-scoping (read this before writing feature code)

`orgs` owns the multitenancy machinery every other app depends on. Feature agents do not
edit models here; you consume the convention below.

## Getting the current org in a view

Any URL under `/o/<org_slug>/` runs through `OrgContextMiddleware`
(`apps/orgs/middleware.py`), which sets:

- `request.org` — the resolved `Org` (404 if the slug is unknown)
- `request.membership` — the requesting user's `Membership` in that org
  (`None` for superusers, who are allowed through for inspection)

An authenticated non-member gets **403**; an anonymous user is redirected to `LOGIN_URL`.
So inside a feature view you can assume `request.org` is set:

```python
@login_required
def index(request, org_slug):
    runs = DropRun.objects.for_org(request.org)
    ...
```

`org_slug` is always a view kwarg (the middleware keys on it) — keep it in your URL
patterns even if the view body ignores it.

## Scoping every query

All domain models inherit `OrgScoped` (`apps/orgs/scoping.py`), which gives them an `org`
FK and a manager with `.for_org(org)`:

```python
DropRun.objects.for_org(request.org)          # correct
DropRun.objects.all()                          # WRONG — leaks across tenants
```

Never query a domain model without scoping to an org.

## Role gating

Use `apps.orgs.mixins.RequireRoleMixin` (CBVs) or check `request.membership.role`
against `apps.orgs.models.MembershipRole` (`admin` / `steward` / `member`).

## URL + namespace map

Feature apps are mounted flatly in `config/urls.py` under `/o/<org_slug>/<feature>/`, so
each app owns a top-level namespace: `drops`, `pie`, `votes`, `sortition` (the *Committee*
tab), `exports`, `tasksources`. Reverse with the org slug:

```django
{% url 'drops:index' org_slug=request.org.slug %}
```

Non-org names live under the `orgs` namespace: `orgs:landing`, `orgs:onboarding`,
`orgs:dashboard`.

## Members page — admin actions

`/o/<slug>/members/` (admin-only) manages people and invites. Beyond role/rate:

- **Grant starting value** — the per-member *Stake* column records an
  `OpeningBalance` (`member_grant_value`), giving a member equity for pre-pie
  work. Additive: each grant is its own row. Enters `compute_pie` like any
  opening balance and reshapes shares proportionally.
- **Delete invite** — `invite_delete` hard-removes an invite row at any status
  (revoke only marks a *live* link dead and is blocked once accepted). Deleting
  an invite never touches a membership; anyone who already joined stays a member.

Members edit their own public fields (name, photo, bio) at `/accounts/profile/` —
self-serve, no admin in the loop.

## API-first

Each app has its own DRF router in `<app>/api.py`, included at
`/api/v1/<app>/`. Register your viewsets there; do not touch another app's `api.py`.
Scope every queryset to `request.org`.
