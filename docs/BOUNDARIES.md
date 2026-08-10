# GovKit: Boundaries

> Read this before you add a table, a page, or a "let me just store this here".
> The default answer is: GovKit owns the earnings record and the org, and points
> at everything else.

## What GovKit owns vs. what it references

| GovKit OWNS (it is the record) | Lives elsewhere; GovKit only references |
|---|---|
| Orgs, memberships, roles, invites (`apps/orgs`) | Who a person *is*, notes, history → **abra** |
| The earnings record: drop runs, issued lines (`apps/drops`) | Tasks and their status → **the team's own tracker** (Taiga/Marten) |
| The pie — shares derived from issued lines (`apps/pie`) | Contacts and outreach → **Odoo CRM** |
| Votes and sortition draws off the same record (`apps/votes`, `apps/sortition`) | Login and identity → **LinkedTrust SSO** (trust_claim_backend as OIDC provider) |
| Per-org task-source config and the valuation mirror (`apps/tasksources`) | Trust claims, attestations → **LinkedTrust** |
| The genesis checklist as *events* — what a team did, appended (`apps/orgs/genesis.py`) | The curriculum text itself → code (`genesis.py` MODULES) + the reading page in **workers.vc** |
| Exports (`apps/exports`) | Chrome, landing, the dash page → **workers.vc** |
| Interest and sponsorship offered from outside (`apps/commons`: `VentureInterest`, `SponsorPledge`) | The pages that collect them → **workers.vc** (`/sponsor/`, the venture join pages) |

## The two rules that keep this untangled

**One home per fact.** If a fact already lives in the tracker, abra, the CRM, or
LinkedTrust, GovKit references it. `TrackedTask` is a *mirror* for valuation, not
a second task list — the tracker stays the record, and sync refreshes the mirror.

**The earnings record is immutable once issued.** A share is never a number
someone typed in. It traces back to the task that earned it. Adjustments happen
in a drop run, with a reason, before approval — never by editing an issued line.

## Separation of concerns

- **GovKit is the org and equity app.** It is not the dashboard: the cohort dash
  lives on the workers.vc apex and composes cards from peer apps.
- **A card on the dash is a web component owned by the app that owns the fact.**
  `<govkit-pie>`, `<govkit-tasks>` ship from `static/embed/govkit.js` and read
  GovKit endpoints with the member's own session. The host page renders chrome,
  never data. Don't put GovKit data into a workers.vc template.
- **API-first.** Every UI action has an endpoint. Org scoping is by path
  (`orgs/<org_slug>/…`), which is what makes `OrgContextMiddleware` resolve
  `request.org` and enforce membership. Don't hand-roll a membership check, and
  don't scope by query param.
- **Adapters hold the vendor.** Taiga specifics live in
  `apps/tasksources/adapters.py`. Nothing outside an adapter should know what
  tracker a team uses.

## Code layout

```
apps/<domain>/     models, services (the logic), api.py (DRF), views.py (HTML), urls.py
static/embed/      govkit.js — the web components other apps' pages embed
docs/design/       pattern-language.md — how it should look
```

Business logic goes in `services.py`, not in a view and not in a serializer.

## How your work lands

**Open a pull request.** Don't push to `main`. The shared-VM rule about
committing straight to main is for the people who own the repo; anyone else
works on a branch and opens a PR, so someone sees it before it deploys. Small
PRs, often, beat one big one.

Pushing to `main` deploys. That is another reason to open a PR instead.

## Ask, don't guess

These are not forbidden. They are the things where guessing makes a mess that
someone else has to unpick, so **stop and ask on the board (`scratch.md`) or ask
a person**:

- A fact that could live in two places, or a new table for something another
  system already knows.
- A new service, database, repo, or system dependency.
- Anything that changes what an issued line, a share, or a vote weight means.
- A migration that is not additive, or that touches issued equity.
- Editing another app's data, or reaching into another app's database.
- A UX pattern you are inventing rather than following
  (`docs/design/pattern-language.md`, `UX_PRINCIPLES.md`) — look up the standard
  one first.
- Access you don't have: a path, a permission, a server. That boundary is
  usually deliberate. Ask how the team does it.

## Also read

`UX_PRINCIPLES.md` before touching anything a person looks at.
`docs/design/pattern-language.md` for how it should look.
`scratch.md` — the coordination board; read the tail, announce what you pick up.
`../amebo/docs/BOUNDARIES.md` — the same discipline from amebo's side.
