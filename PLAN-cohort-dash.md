# Cohort Dash — cross-repo plan (govkit copy)

2026-07-19. One of six coordinated plan files, one per repo:
`workers.vc`, `govkit`, `amebo`, `marten`, `crm-outreach-runner`, `earnkit` —
each named `PLAN-cohort-dash.md` at the repo root. The **Architecture**
section is identical in all six; the **This repo** section is per-repo.
Work in parallel; commit and push regularly; each repo only implements its
own section and consumes the others' contracts as written here.

## Architecture (shared across all six repos)

**Goal.** Land accelerator teams on a real dashboard: the v3 design
(demos.linkedtrust.us/workersvc-design/dashboard.html) grown out of the
existing `/dash/` page, plus a mentor view, so invites can go out now.

**Principle** (amebo docs/DASHBOARD.md): the dash is an orientation
surface, not a workspace. Every fact lives in the tool that owns it; the
dash renders read-only cards and every card expands into the owning app
(Marten, GovKit, CRM, amebo). No fact is copied into the dash's DB.

**Mechanism: web components, one bundle per owning app.** Following the
existing amebo embed pattern (`amebo/embed/amebo.js`): each app ships a
vanilla-JS custom-elements bundle as a static file from its own origin.
The dash page includes the scripts and mounts the tags. No build step, no
framework, no shared library.

**Auth: SSO + same-site cookies + CORS allowlist.** Everything runs under
`*.workers.vc`, and every app logs in via LinkedTrust OIDC
(live.linkedtrust.us). Because all hosts share the registrable domain
`workers.vc`, each app's `SameSite=Lax` session cookie IS sent on a
credentialed fetch from the dash page — the only missing layer is CORS
response headers. So each app: (1) allowlists `https://workers.vc` (and
`https://www.workers.vc`) for CORS **with credentials**, scoped to its
JSON API paths; (2) authenticates component fetches with its normal
session cookie (`credentials: 'include'`). A component whose upstream
returns 401/403 renders nothing (the existing dash behavior) — signed-out
or non-member visitors just see fewer cards. Never render placeholder or
demo data.

**Org scoping.** The dash is per-team: `workers.vc/dash/<org-slug>/`.
The org slug is the shared tenant key across GovKit (`Org.slug`), amebo
(`organizations.slug` / instance orgs), Taiga (project slug), and Odoo
(DB `crm-<slug>`, host `crm-<slug>.workers.vc`) — provisioned together by
`earnkit/playbooks/add-team.yml`. Components take the org via a
`data-org` attribute where the owning app needs it (GovKit), or resolve
it server-side from the authenticated identity (amebo — org is never a
component attribute there).

**Card → owner map** (v3 design → who ships the component):

| Card | Owner | Component | Expand target |
|---|---|---|---|
| The pie | GovKit | `<govkit-pie>` | `dash.workers.vc/o/<org>/pie/` |
| Earned on tasks (hours feed) | GovKit | `<govkit-feed>` | `dash.workers.vc/o/<org>/pie/` |
| Curriculum tracker | GovKit (genesis checklist) | `<govkit-checklist>` | `dash.workers.vc/o/<org>/` |
| Tasks to do | GovKit (tasksources → Taiga) | `<govkit-tasks>` | `martin.workers.vc/p/<org>/board` |
| Money | GovKit (projects app) | `<govkit-money>` | `dash.workers.vc/o/<org>/projects/` |
| Reach out (CRM) | crm-outreach-runner (Odoo) | `<crm-reachout>` | `crm-<org>.workers.vc` Outreach Runner |
| Ask amebo | amebo (exists) | `<amebo-ask>` | `amebo.workers.vc` |
| Campaigns / GTM board | amebo (`/api/organizations/board`) | `<amebo-board>` (phase 2) | org context repo / CRM / Taiga links |
| Whiteboard | amebo (phase 2) | — | amebo whiteboard |
| Tools row, faces, launch card | workers.vc server-side | — | — |

**Mentors.** No new role system. A mentor is a person with GovKit
`Membership` rows in multiple orgs (the accelerator org plus team orgs).
`GET dash.workers.vc/api/v1/accounts/me/` already returns
`memberships[{org_slug, org_name, role}]` — the dash uses it (via the
same CORS/session mechanism) to render an org switcher and a mentor
overview listing every org the viewer belongs to. Mentor booking info
(calendar_url/time_level) already lives in workers.vc's ledger.

**Deploys.** Push to main deploys workers.vc / govkit / amebo / marten
via GitHub Actions → `/opt/earnkit/bin/update-*` (service restart). Odoo
addons and nginx/env changes deploy by ansible run (see earnkit plan).

**Sequencing.** GovKit's CORS + bundle is the critical path (4 of the 8
cards); everything else proceeds in parallel against these contracts, and
each card goes live the moment its owner ships.

---

## This repo: govkit — CORS, three JSON additions, and the embed bundle

### Current state (verified 2026-07-19)

Everything the dash needs mostly exists, session-authed and org-gated by
`OrgContextMiddleware`:

- `GET /api/v1/accounts/me/` — identity + memberships (apps/accounts/api.py:35).
- `GET /api/v1/pie/orgs/<slug>/summary/` — slices with share_pct,
  issued_total, lines→tasks drill-down (apps/pie/api.py:651).
- `GET /api/v1/pie/orgs/<slug>/standing/` — personal standing.
- `GET /api/v1/tasksources/orgs/<slug>/tasks/` — **done** tracked tasks
  only (valuation pipeline; not open work).
- Genesis checklist — `ChecklistItem` (apps/orgs/models.py:283),
  5 modules in apps/orgs/genesis.py, rendered only as HTML on the org
  dashboard; no JSON endpoint.
- Projects/money — apps/projects (Project, Deal, Split, Payout) with
  per-project `summary/`; no single portfolio endpoint.
- **No CORS anywhere** (no django-cors-headers; no ACAO headers emitted).
  Session cookie SameSite=Lax (default), which works same-site from
  workers.vc — CORS headers are the only blocker.
- No web components / embed assets.
- Taiga connection per org: `TaskSourceConfig` holds base_url + encrypted
  api_token (apps/tasksources/models.py:24) — GovKit is the one place
  that can serve *open* Taiga tasks to the dash without new browser auth.

### Work items (in order)

1. **CORS** — add `django-cors-headers`:
   - `CORS_ALLOWED_ORIGINS` from env (cohort value:
     `https://workers.vc,https://www.workers.vc`)
   - `CORS_ALLOW_CREDENTIALS = True`
   - `CORS_URLS_REGEX = r"^/api/"` (API only; HTML pages unaffected)
   - Leave session cookie SameSite=Lax (same-site is sufficient).
   - env template change rides earnkit (see its plan); add to .env.sample.

2. **Checklist JSON** — `GET /api/v1/orgs/<slug>/checklist/`
   (session, member-gated, same DRF conventions):
   ```json
   {"org_slug": "wayfern",
    "modules": [{"key": "exist", "title": "It exists", "week": 1,
                 "done": 4, "total": 4,
                 "items": [{"id": 12, "title": "…", "done": true}]}]}
   ```
   Read-only; toggling stays in the GovKit HTML dashboard (the expand
   target).

3. **Open-tasks proxy** — `GET /api/v1/tasksources/orgs/<slug>/tasks/open/`
   (session, member-gated). Live fetch through the org's `TaigaAdapter`
   (status not closed), cached ~60s server-side; do NOT touch the
   TrackedTask valuation pipeline. Shape:
   ```json
   {"tasks": [{"external_id": "123", "ref": 45, "subject": "…",
               "assignee_label": "jo", "status": "In progress",
               "external_url": "https://taiga.workers.vc/…"}],
    "fetched_at": "2026-07-19T…Z"}
   ```
   Include the story `ref` and project slug if the adapter can supply
   them so the dash can deep-link
   `martin.workers.vc/p/<slug>/board?story=<ref>`.

4. **Portfolio JSON** — `GET /api/v1/projects/orgs/<slug>/portfolio/`
   (session; **member**-readable — reads are not steward-only; verify and
   loosen read permissions if the current viewsets gate reads):
   ```json
   {"currency": "USD", "budget_total": "16500.00", "paid_total": "7500.00",
    "projects": [{"id": 3, "name": "Pima Kitchens pilot", "kind": "client",
                  "status": "active", "budget_total": "4500.00",
                  "paid_total": "4500.00", "promised_pct": "100.0"}]}
   ```
   Empty projects → `{"projects": []}` (dash hides the card).

5. **Embed bundle** — `static/embed/govkit.js`, vanilla JS custom
   elements, amebo-embed style (no deps, no build). All components take
   `data-up` (GovKit origin, e.g. `https://dash.workers.vc`) and
   `data-org`; every fetch is `credentials:'include'`; any non-200 →
   render nothing and set `hidden` on the host element. Components:
   - `<govkit-pie>` — pie SVG + legend from `pie/orgs/<org>/summary/`
     (port the drawing code from workers.vc `cohort_dash.html`, which
     this bundle replaces; keep textContent-only DOM writes).
   - `<govkit-feed data-limit="8">` — flattened
     slices→lines→tasks rows: member, task subject, final_value, unit.
   - `<govkit-checklist>` — modules with done/total + item ticks, from
     item 2.
   - `<govkit-tasks data-limit="6" data-tasks-app="https://martin.workers.vc">`
     — open tasks from item 3; row links prefer the marten deep link,
     falling back to `external_url`.
   - `<govkit-money>` — portfolio rows + signed/received totals from
     item 4.
   Ship a `static/embed/demo.html` for manual testing.

6. **Login handoff** — verify `/accounts/linkedtrust/login/` honors a
   `?next=` absolute-path redirect after callback; if not, add support
   for same-site absolute URLs (`https://workers.vc/...`) via an
   allowlist, so the dash's sign-in chip can round-trip. Document the
   final URL here when done.

7. **Mentor memberships** — no code expected: mentors get Membership
   rows (role member or steward) in each org they mentor via the normal
   admin invite/member flow. Confirm `accounts/me` returns them all.

### Definition of done

From a page on `https://workers.vc` with a GovKit session cookie:
credentialed fetches to `accounts/me`, `pie/…/summary`, `orgs/…/checklist`,
`tasksources/…/tasks/open`, `projects/…/portfolio` all succeed with CORS;
`static/embed/govkit.js` mounts all five components against a real org;
non-members get 403s and empty components. Nothing about drops/valuation
changed.
