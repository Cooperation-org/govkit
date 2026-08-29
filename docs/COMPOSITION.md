# Composing a dashboard

**This is the master document for how the workers.vc tools compose into one page.**
Every repo in the set links here. It describes the mechanism, the contracts, and how
to build a *second* dashboard for a different audience out of the same parts.

For who owns which fact, read `docs/BOUNDARIES.md` in this repo and in `workers.vc`
and `amebo`. This document is only about composition.

`PLAN-cohort-dash.md` (repo root) is the dated build plan and its status log — history,
not contract. When the two disagree, this file is the contract.

---

## The shape: one shell, many components

A dashboard is a **shell** page that mounts **components** served by the apps that own
the data. No app's data is copied into the shell. Every card expands into the app
that owns it.

```
   ┌─── SHELL ───────────────────────────────────────────────────────────┐
   │  workers.vc  /dash/<org-slug>/          repo: workers.vc            │
   │  owns: nav, layout, copy, the org slug, and every peer URL (env)    │
   │                                                                     │
   │   <govkit-checklist>  <govkit-tasks>  <govkit-feed>                 │
   │   <govkit-money>      <crm-reachout>                                │
   └──────────┬──────────────────────────────────────┬───────────────────┘
              │ <script src>                         │ <script src>
              ▼                                      ▼
   dash.workers.vc                        crm-<org>.workers.vc
   /static/embed/govkit.js                /crm_outreach_runner/static/src/embed/
   repo: govkit                              crm-reachout.js
   Django + DRF + Postgres                 repo: crm-outreach-runner (Odoo 17 addon)

   Component providers not currently mounted on this shell:
   amebo.workers.vc/embed/amebo.js        repo: amebo   (<amebo-ask>, <amebo-goals>, …)

   ┌─── EXPAND TARGETS (full apps, linked to, never embedded) ───────────┐
   │  dash.workers.vc/o/<org>/…   GovKit   pie, drops, votes, members    │
   │  marten.workers.vc/p/<org>/board?story=<ref>   Chiku over Taiga     │
   │  crm-<org>.workers.vc/       Elm (fast front) and Odoo under /web   │
   │  amebo.workers.vc/           the team agent                         │
   └─────────────────────────────────────────────────────────────────────┘
```

Verified live 2026-08-29: `https://workers.vc/dash/` serves exactly the five
components and two script tags above.

### The three roles a repo can play

| Role | Means | Repos today |
|---|---|---|
| **Shell** | Owns a page for an audience: nav, layout, copy, and the config that says where every peer app lives. Site-specific by design. | `workers.vc` |
| **Component provider** | Ships one static custom-elements bundle from its own origin. Knows nothing about any shell. | `govkit`, `amebo`, `crm-outreach-runner` |
| **Expand target** | A full app a card links out to. Publishes a stable deep-link URL shape. | `chiku`, `elm`, plus every provider's own UI |

A repo can be two of these. `govkit` is a component provider *and* an expand target.

---

## Component catalog

Every tag currently defined, across all repos. Attributes are the component's own
contract; the shell supplies the values.

**GovKit** — `<govkit-base>/static/embed/govkit.js` (`govkit/static/embed/govkit.js`)

| Tag | Reads | Attributes beyond `data-up` |
|---|---|---|
| `<govkit-pie>` | `GET /api/v1/pie/orgs/{org}/summary/` | `data-org` |
| `<govkit-feed>` | same summary, flattened to rows | `data-org`, `data-limit` |
| `<govkit-checklist>` | `GET /api/v1/orgs/{org}/checklist/` | `data-org`, `data-reading`, `data-week` |
| `<govkit-tasks>` | `GET /api/v1/tasksources/orgs/{org}/tasks/open/` | `data-org`, `data-limit`, `data-tasks-app` |
| `<govkit-money>` | `GET /api/v1/projects/orgs/{org}/portfolio/` | `data-org` |
| `<govkit-activity>` | `GET /api/v1/commons/orgs/{org}/attention/` | `data-org` |
| `<govkit-ventures>` | `GET /api/v1/commons/ventures/` | `data-limit` (no org — every venture) |
| `<govkit-news>` | `GET /api/v1/commons/news/mine/` | `data-limit` (no org — the viewer's own rail) |

**amebo** — `<amebo-base>/embed/amebo.js` (`amebo/embed/amebo.js`; details in `amebo/embed/README.md`)

`<amebo-ask>`, `<amebo-goals>`, `<amebo-goal>`, `<amebo-claws>`, `<amebo-create-claw>`,
`<amebo-digest>`, `<amebo-skills>`. **Org is never an attribute here** — amebo resolves
it from the authenticated session.

**CRM** — `crm-<org>.workers.vc/crm_outreach_runner/static/src/embed/crm-reachout.js`

`<crm-reachout>` (`GET /outreach/api/queue?limit=`), `<crm-heard>`. The org is the
hostname; the Odoo session cookie is the auth.

**Nav** — `workers.vc/static/embed/cohort-nav.js`

`<cohort-nav>` — the 32px bar every workers.vc surface mounts, so there is one copy of
the cohort's map. Attributes `data-org`, `data-domain`, `data-current`, `data-site-url`.

---

## The four contracts

### 1. Mount

- One static file per owning app, served from that app's own origin. Vanilla custom
  elements: no build step, no framework, no shared library, no shadow DOM.
- Every component takes **`data-up`** — the owning app's browser-reachable base.
  It never contains a hostname of its own.
- Org arrives as **`data-org`** where the owning app needs it, or is resolved
  server-side from the session (amebo), or from the hostname (CRM).
- Every fetch is `credentials: 'include'`.
- **Any non-200, bad payload, or empty dataset renders nothing and sets `hidden` on
  the host element.** Signed-out and non-member visitors simply see fewer cards.
  Never a placeholder, never demo data.
- DOM writes are `textContent`/attribute only.
- The host page styles by tag selector and CSS variables.

### 2. Auth

All hosts sit under one registrable domain (`workers.vc`) and every app logs in via
LinkedTrust OIDC (`live.linkedtrust.us`). Because the hosts are same-site, each app's
`SameSite=Lax` session cookie rides a credentialed fetch from the shell. The only extra
layer each provider needs is **CORS with credentials**, allowlisting the shell origin
and scoped to its JSON API paths.

In GovKit that is `CORS_ALLOWED_ORIGINS` (env), `CORS_ALLOW_CREDENTIALS = True`,
`CORS_URLS_REGEX = r"^/api/"`.

There is **no sign-in cascade**. The shell needs only its own session; each peer app
logs the person in on first click into it.

### 3. Expand target

A card is read-only. The way out is a deep link the owning app guarantees:

| Target | Shape |
|---|---|
| GovKit org page | `<govkit-base>/o/<org>/{pie,drops,votes,members,projects,open}/` |
| Chiku (Taiga) board | `<tasks-app>/p/<project-slug>/board?story=<ref>` |
| Odoo CRM pipeline | `crm-<org>.workers.vc/web#action=crm.crm_lead_action_pipeline&model=crm.lead&view_type=kanban` |
| Elm (fast CRM front) | `crm-<org>.workers.vc/` and `/c/<campaignId>` |
| amebo | `<amebo-base>/dashboard/list` |

Changing one of these shapes breaks every shell. Treat them as published API.

### 4. Config — all URLs come from env, in the shell

**No component and no app knows another app's hostname.** Every cross-app URL is
configuration read by the shell and handed down as an attribute. In `workers.vc` that
is `workersvc/settings.py` via `python-decouple`, documented in `.env.example`:

| Variable | Hands the shell |
|---|---|
| `GOVKIT_PUBLIC_URL` | `data-up` for every `govkit-*` tag, and the bundle's `<script src>` |
| `AMEBO_PUBLIC_URL` | `data-up` for every `amebo-*` tag |
| `MARTEN_PUBLIC_URL` | `data-tasks-app` for `<govkit-tasks>` |
| `CRM_URL_PATTERN` | `https://crm-{org}.workers.vc` → `data-up` for `<crm-reachout>` |
| `GOVKIT_ORG_SLUG` | the accelerator's own slug, so the shell knows its dash from a team's |
| `AMEBO_API_BASE`, `AMEBO_API_TOKEN` | server-side reads (the tools row) |
| `COHORT_CHAT_URL`, `COHORT_CALENDAR_URL` | the cohort's links in the top bar |

An unset variable means the card is **omitted**, not broken. That is the whole
mechanism by which a different shell drops a card it does not want.

**Known gaps to close before a second shell** (verified 2026-08-29, all in `workers.vc`):

- `doorway/static/embed/cohort-nav.js` builds every tool URL by string concatenation
  off a registrable domain — `'https://marten.' + domain`, `'https://crm-' + org +
  '.' + domain`, `'https://dash.' + domain`, `'https://amebo.' + domain`. It works for
  one cohort on one domain; it cannot serve a shell whose tools are elsewhere.
- The `<crm-reachout>` bundle path is written into `cohort_dash_v3.html` (line 918).
- `COHORT_CHAT_URL` / `COHORT_CALENDAR_URL` are duplicated as `COHORT_CHAT` /
  `COHORT_CALENDAR` constants inside `cohort-nav.js`; the settings comment says to
  edit both.
- `settings.py` defaults point at the *other* deployment: `TAIGA_API_BASE`
  (`taiga.linkedtrust.us`), `IDEAS_ADD_URL` (`marten.linkedtrust.us/p/ideas/board`).
  Set in env on the cohort VM, but a fresh deploy inherits the wrong host.

---

## Building a second shell

The volunteer dashboard is a new shell: different audience, different nav, tasks and
CRM but no equity, possibly votes. Nothing in the component providers changes.

1. **New repo (or new app in an existing site repo) for the shell only.** It holds the
   page, the nav, the copy, and the env that names its peers. Site-specific facts live
   here and nowhere else.
2. **Pick the tags you want.** Tasks: `<govkit-tasks>`. CRM: `<crm-reachout>`. Votes:
   `<govkit-*>` when the votes component ships (Milestone 2 — the models exist, the
   pages are placeholders). Equity: simply do not mount `<govkit-pie>`, `<govkit-feed>`,
   `<govkit-money>`. Leaving a variable unset omits its card.
3. **Add the shell's origin to each provider's CORS allowlist** (env change only, no
   code) and make sure the shell's hosts are same-site with the providers, or the
   session cookie will not ride the fetch. Cross-site needs a different auth story —
   stop and design it, do not weaken the cookie.
4. **Write your own nav.** Do not reuse `cohort-nav.js`: it encodes the cohort's map and
   derives hosts from `workers.vc`. Reuse the *pattern* (one thin bar, one file, mounted
   by every surface, quiet when signed out).
5. **Do not add a database table to the shell.** If a fact has no owner yet, stop and
   decide which app owns it before building the card.

A visual mock of this shape for a volunteer audience already exists:
`demos.linkedtrust.us/rtv-volunteer-dash/` (sample data, nothing wired).

## Adding a card to an existing shell

1. Decide which app owns the fact. If none does, that is the design question — settle
   it first.
2. Add the JSON endpoint in the owning app (member-gated, session-authed, under `/api/`).
3. Add the component to that app's bundle, following the mount contract above, and add
   a row to the catalog in this file.
4. In the shell: add the env variable, hand it down as `data-up`, mount the tag.
5. A card with nothing to show must disappear, not render empty.

---

## Running the composition locally

Every repo in the set runs on a laptop. You do not need the cohort VM, and you do not
need all of them at once — mount only the components you are working on and the rest
of the cards stay absent, which is the designed behavior.

**Ports.** Use `localhost` for everything. Different ports on `localhost` are the same
*site*, so each app's `SameSite=Lax` session cookie still rides a credentialed fetch
from the shell — exactly as it does across `*.workers.vc`. Only CORS has to be opened.
The convention the repos already assume:

| App | Local URL | Start |
|---|---|---|
| GovKit | `http://localhost:8010` | `cp .env.sample .env` then `WEB_PORT=8010 docker compose up --build` |
| workers.vc shell | `http://localhost:8001` | `venv/bin/python manage.py runserver 8001` |
| amebo backend | `http://localhost:8000` | `cd backend && PYTHONPATH=. venv/bin/uvicorn src.api.main:app --reload --port 8000` |
| amebo frontend | `http://localhost:3087` | `cd frontend && npm run dev -- -p 3087` |
| Chiku | `http://localhost:5173` | `npm install && npm run dev` |
| Elm | `http://localhost:5174` | `npm install && npm run dev -- --port 5174` |

**Opening a provider to your local shell.** In the provider's `.env`, add your shell's
origin to its CORS allowlist — GovKit: `CORS_ALLOWED_ORIGINS=http://localhost:8001`.
Nothing else changes. A provider you have not opened simply answers with no CORS
headers and its cards render nothing.

**Pointing the shell at local peers.** The shell's `.env` is the only place that names
peers, so local development is one file:

```
GOVKIT_PUBLIC_URL=http://localhost:8010
GOVKIT_BASE_URL=http://localhost:8010
AMEBO_PUBLIC_URL=http://localhost:8000
MARTEN_PUBLIC_URL=http://localhost:5173
CRM_URL_PATTERN=
```

`CRM_URL_PATTERN` empty omits the reach-out card — Odoo is the one peer that is not
worth running locally for shell work.

**Working against live peers instead.** Point the shell's `.env` at
`https://dash.workers.vc` etc. The cards will render nothing: `localhost` is not
same-site with `workers.vc`, so no session cookie rides the fetch and CORS does not
list your origin. That is correct behavior, not a bug. Run the provider locally, or
work on the shell's own chrome.
