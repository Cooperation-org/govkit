# GovKit — coordination board

**Read this FIRST. Append status/questions; never delete others' notes.**

GovKit = open-source **earned-governance toolkit**. Teams earn ownership/governance
weight from peer-reviewed, approved tasks in their tracker; one earnings record powers
equity (the pie), work-weighted votes, and work-weighted sortition. Self-hostable AND
runnable by us as a multitenant service. Full contract:
`~golda/work/7-6-2026-govkit-build-instructions.md` (SETTLED — do not re-litigate).

---

## CURRENT STATE  (updated 2026-07-06 by orchestrator session)

- **Phase:** Milestone 1 build, orchestrated across subagents.
- **Stack (settled):** Django 5.x + DRF, Postgres, server-rendered templates + HTMX,
  API-first. Docker Compose for self-host. LinkedTrust OIDC default login + Google secondary.
- **DB for dev/CI:** Docker Compose Postgres (in-repo). The production `govkit` DB on
  VM 100 is a **deploy-time blocker** (see Q1) — not needed to build/test.
- **Repo:** `Cooperation-org/govkit`, checkout `/opt/shared/repos/govkit/` (private for now — see Q6).

### Progress log
- [x] Recon: references read (django-linkedtrust-auth, legacy `GovernanceToken/earning`,
      oauth-login-pattern, postgres-access, app-registry). Docker + Compose confirmed on VM 200.
- [x] M1.1 Scaffold (Django project, Compose, env+base-path, CI, app skeletons)
- [x] M1.2 Core models + multitenancy + admin
      (verified against Compose Postgres: migrations + `check` clean, 16 tests green,
       black/flake8 clean, live-server curl of landing/org-dashboard/base-path OK.
       See "Foundation notes for feature agents" below.)
- [ ] M1.3 Auth + onboarding (LinkedTrust OIDC default, Google secondary)
- [ ] M1.4 Taiga adapter (REST API, both valuation modes, missing-value queue)
- [ ] M1.5 Drop runs (open → review → adjust w/ reason → approve → issued; audit trail)
- [ ] M1.6 Pie (org shares + traceability drilldown, personal standing)
- [ ] M1.7 Import (opening balances CSV) + export (generic CSV, Slicing Pie format)
- [ ] M2 Votes, Sortition, Docs
- [ ] Deploy: systemd `tmp-govkit-backend.service`, nginx `govkit.conf`,
      `demos.linkedtrust.us/govkit/`, register in app-registry

---

## QUESTIONS FOR FABLE / GOLDA  (answer inline under each — don't delete the question)

**Blockers (config/secrets/infra I cannot self-serve — build proceeds with `.env.sample`
placeholders and stubs until these land):**

- **Q1 — Production DB on VM 100.** Build instructions authorize a new `govkit` Postgres
  DB on VM 100, but creation requires the superuser password on the Proxmox host
  (`cobox/scripts/create-app-db.sh`), which I must not run and cannot access from VM 200.
  **Need:** someone runs `sudo ./create-app-db.sh govkit` on the Proxmox host and drops the
  printed `govkit_owner` / `govkit_user` creds into the deploy `.env`. (Dev/CI use Compose
  Postgres, so this only blocks the VM 200 deploy.)
  - _Answer:_

- **Q2 — LinkedTrust OIDC client.** Need a confidential OIDC client registered at
  `https://live.linkedtrust.us` with redirect_uri
  `https://demos.linkedtrust.us/govkit/api/v1/auth/linkedtrust/callback`, scopes
  `openid email profile trust` → yields `LINKEDTRUST_CLIENT_ID` / `LINKEDTRUST_CLIENT_SECRET`.
  - _Answer:_

- **Q3 — Taiga read-only API token** for the adapter to sync stories via Taiga REST (NOT DB).
  Which Taiga base URL + a scoped auth token? (Instructions say ask Golda; do not touch the Taiga DB.)
  - _Answer:_

- **Q4 — Google OAuth client** (secondary login): `GOOGLE_OAUTH_CLIENT_ID` / `_SECRET`,
  JS origin `https://demos.linkedtrust.us`, redirect (code flow) `.../govkit/auth/google/callback`.
  - _Answer:_

**Design questions from the build doc (Golda to decide — I will NOT decide these; model
supports all options, defaults noted so the build isn't blocked):**

- **Q5a — Hourly rates:** single org-wide default rate, or per-member from day one?
  (Model stores per-member; default UX = org-wide default with per-member override.)
  - _Answer:_
- **Q5b — Historical import source of truth on conflict:** legacy `issued_cook` table vs
  the totals spreadsheet — which wins?
  - _Answer:_
- **Q5c — Budget policy defaults for our own org:** weekly assignable amount? self-assign cap?
  (Default seeded = unlimited / soft-warn only.)
  - _Answer:_
- **Q5d — Our instance's org slug / unit:** `whatscookin / COOK` or `linkedtrust / COOK`?
  - _Answer:_
- **Q5e — Taiga hours field:** native Taiga points, or a custom attribute? (Adapter maps
  either; question is what OUR Taiga will use.)
  - _Answer:_

**Orchestrator defaults chosen (flag if wrong):**
- **Q6 — Repo visibility:** created **private** (private→public is a deliberate publish
  action; left to Golda). Flip to public when ready to open-source.
  - _Answer:_
- **Q7 — License:** **MPL-2.0**, matching `django-linkedtrust-auth` and team pattern.
  - _Answer:_

---

## Foundation notes for feature agents  (added by M1.1/M1.2 session — this is your contract)

The schema + multitenancy + admin are DONE and verified against a real Postgres. Feature
agents add **views / services / adapters / templates / DRF viewsets / tests inside their
own app only**. Do NOT edit models or migrations — the schema is frozen so parallel work
can't collide. If you think a model is genuinely missing, write it here first, don't add it.

### App ownership (one agent per app; stay in your dir)
| App | Dir | You fill in | Models (frozen) |
|---|---|---|---|
| accounts | `apps/accounts/` | OAuth login (LinkedTrust OIDC default + Google), replacing the dev-login stub | `User` |
| orgs | `apps/orgs/` | onboarding wizard logic, invites, roles UI | `Org, ValuationConfig, Membership, OpeningBalance` |
| tasksources | `apps/tasksources/` | Taiga REST adapter, sync command, missing-value queue | `TaskSourceConfig, TrackedTask` |
| drops | `apps/drops/` | open→review→adjust→approve flow; `compute_line_value` body | `DropRun, DropLine` |
| pie | `apps/pie/` | pie + personal-standing pages; `compute_pie` body | (none — read model) |
| exports | `apps/exports/` | opening-balance CSV import + CSV/Slicing-Pie export | `ImportBatch` |
| votes | `apps/votes/` (M2) | create/vote/weighted-tally | `Vote, Ballot` |
| sortition | `apps/sortition/` (M2) | seeded work-weighted draw | `SortitionDraw` |

### Org-scoping convention (READ `apps/orgs/README.md`)
- `OrgContextMiddleware` (`apps/orgs/middleware.py`) runs on every `/o/<org_slug>/…` route
  and sets **`request.org`** and **`request.membership`**. Non-member → 403; anon → login;
  unknown slug → 404; superuser passes with `membership=None`.
- Every domain model inherits `OrgScoped` (`apps/orgs/scoping.py`) → use
  **`Model.objects.for_org(request.org)`**, never `.objects.all()`.
- Role gating: `apps.orgs.mixins.RequireRoleMixin` (CBV) or check `request.membership.role`
  against `apps.orgs.models.MembershipRole` (`admin|steward|member`).
- Choice vocab (`ValuationMode, WeightWindow, BudgetPeriod, BudgetEnforcement`) lives in
  `apps/orgs/models.py` — import from there, don't redefine.

### URL include structure
- Feature HTML routes are mounted **flatly** in `config/urls.py`:
  `path("o/<slug:org_slug>/drops/", include("apps.drops.urls"))`, etc. → each app owns a
  top-level namespace: `drops, pie, votes, sortition` (the *Committee* tab), `exports,
  tasksources`. Reverse with the slug: `{% url 'drops:index' org_slug=request.org.slug %}`.
- Non-org names: `orgs:landing`, `orgs:onboarding`, `orgs:dashboard`. Auth: `accounts:login`,
  `accounts:logout`. Add your routes **inside your own `apps/<app>/urls.py`** (keep the
  `org_slug` kwarg on org-scoped patterns — the middleware needs it).
- Every view must use `{% url %}`/`{% static %}` so `BASE_PATH` (FORCE_SCRIPT_NAME) applies.

### DRF (API-first — every UI action also needs an endpoint)
- One router **per app** at `apps/<app>/api.py`, included at `/api/v1/<app>/` in
  `config/urls.py`. Register your viewsets in your own `api.py`; do not touch another app's.
  Scope every queryset to `request.org`.

### Auth seams (accounts agent)
- `settings.py` reads `LINKEDTRUST_*` + `GOOGLE_OAUTH_*` from env (placeholders in
  `.env.sample`). `LINKEDTRUST_USER_HANDLER = "apps.accounts.auth_handlers.get_or_create_user"`.
- Implement `apps/accounts/auth_handlers.py::get_or_create_user(userinfo)` (currently raises).
- Add OAuth entry-point URLs in `apps/accounts/urls.py`; the LinkedTrust include is a
  commented seam in `config/urls.py`. `User` has `auth_provider`/`auth_provider_id` for the
  OIDC subject map. Replace `apps/accounts/views.py::dev_login` (gated by `GOVKIT_DEV_LOGIN`).

### Stubbed service signatures (implement the body, keep the signature)
- `apps/drops/services.py::compute_line_value(membership, tasks, valuation_config) -> Decimal`
  — pre-adjustment earned value; both valuation modes + at-risk multiplier. Docstring is the spec.
- `apps/pie/services.py::compute_pie(org)` — `Σ issued DropLines + OpeningBalances` per
  membership → shares, traceable to source. Docstring is the spec.
- Also stubbed: `apps/tasksources/adapters.py` (Taiga adapter), `apps/exports/services.py`
  (import/export).

### Encrypted secrets
- `TaskSourceConfig.api_token` uses `apps/tasksources/fields.py::EncryptedTextField` (Fernet,
  keyed on `GOVKIT_SECRET_KEY`). Set that env var before saving a token; it raises rather
  than store plaintext.

### Running locally
- `docker compose up -d db` (dev used port **5433** to avoid the host's 5432; compose default
  is 5432 — set `DB_PORT` if needed). `DATABASE_URL=postgres://govkit:govkit@localhost:5433/govkit`.
- `python manage.py migrate && python manage.py seed_org --slug demo --name "Demo" --unit points --email you@example.com --password devpass`.
- `pytest` / `black --check .` / `flake8 .` must stay green. Tests run with `DEBUG=true`
  (plain static storage); prod/CI run `collectstatic` for the manifest storage.
