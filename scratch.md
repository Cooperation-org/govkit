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
- [x] M1.3 Auth + onboarding (LinkedTrust OIDC default, Google secondary) — 54 tests
- [x] M1.4 Taiga adapter (REST API, both valuation modes, missing-value queue) — 22 tests
- [x] M1.5 Drop runs (open → review → adjust w/ reason → approve → issued; audit trail) — 19 tests
- [x] M1.6 Pie (org shares + traceability drilldown, personal standing) — 14 tests
- [x] M1.7 Import (opening balances CSV) + export (generic CSV, Slicing Pie format) — 21 tests
- [x] **M1 INTEGRATED** — 5 feature branches merged to main; audit-trail fields added
      (`DropRun.approved_by`, `DropLine.adjusted_by/adjusted_at`); API convention unified to
      path-based (`/api/v1/<app>/<org_slug>/...`); `value_tag_pattern` greedy default removed.
      **131 tests green** on Postgres, `check`+`makemigrations --check` clean, black/flake8 clean.
      Smoke-tested end-to-end: landing/login/dashboard + all 7 org pages + 3 APIs = 200;
      cross-org non-member = 403. Pushed to `origin/main` (6fa0c7b).
- [x] M2 Votes (weighted, snapshot tally) + Sortition (seeded, reproducible) + Docs
      (README, self-hosting guide, governance-practices from abra) — 34 tests
- [x] **Hardening pass** — all 9 security-review findings fixed (H1 prod SECRET_KEY guard,
      M2 OAuth-takeover refusal, M3 admin token mask, M4 open_run lock, L5 rate privacy,
      L7 txn/HTTP split, L8 secure cookies, L9 fail-safe decrypt, L10 CSV-injection guard).
- [x] **API surface unified** to `/api/v1/<app>/orgs/<org_slug>/…` across all 6 apps.
- [x] **FINAL STATE: 180 tests green** on Postgres; check + migration-check clean; black/flake8
      clean; full end-to-end smoke (all pages + all 6 app APIs = 200; cross-org = 403).
      Pushed to `origin/main`. Port 8062 + app-registry entry registered (deploy pending).
- [x] **LIVE DEMO deployed 2026-07-06** → **https://demos.linkedtrust.us/govkit/**
      (systemd `tmp-govkit-backend.service` :8062 + nginx `app-proxies/govkit.conf`; DB on VM100).
      **TEMPORARY demo config:** `GOVKIT_DEV_LOGIN=1` is ON (public URL) + seeded demo org so it's
      clickable before OIDC lands. **LOCK DOWN after the demo:** set `GOVKIT_DEV_LOGIN=` (unset),
      restart service, once Q2 OIDC creds are in. Real go-live steps remain in `deploy/README.md`.

### Deviations from the build doc (flag for Golda — accept or change)
- **D1 (re item 15) — LinkedTrust OIDC is implemented IN-APP, not via the pip package
  `django-linkedtrust-auth`.** Reason: that package's flow redirects to a *frontend* with
  tokens in the URL fragment (SPA model); GovKit is server-rendered Django with **session**
  login, which the package doesn't do. We vendored a session-based adaptation of its `oidc.py`
  (same OIDC protocol, same live issuer) into `apps/accounts/`, and left the package include a
  commented seam. Net: identical protocol/IdP, one fewer external dependency for self-hosters.
  Accept? → _Answer:_
- **L6 (invite links)** — signed 14-day bearer tokens, multi-use, not email-bound, no
  server-side revocation (frozen schema has no Invite model). Fine for v1, or add
  single-use/email-binding? → _Answer:_ (see security triage above)

### Security review triage (M1 review, 2026-07-06) — core tenant isolation CLEAN
To fix in the hardening pass (after M2 merges):
- **H1** SECRET_KEY insecure default silently used in prod → invite-token forgery. Raise
  `ImproperlyConfigured` when `not DEBUG and SECRET_KEY==default`. **must-fix**
- **M2** OAuth link-by-verified-email can take over a no-provider account (incl. superuser).
  Refuse auto-link to staff/superuser or accounts with a usable password. **must-fix**
- **M3** Fernet `api_token` shown plaintext in Django admin change form → mask/exclude. **must-fix**
- **M4** `open_run` double-count race → `select_for_update` eligible tasks in the txn. **fix**
- **L5** member `hourly_rate` readable by any org member via API retrieve → admin-gate. **fix**
- **L7** sync holds a DB txn open across Taiga HTTP → fetch outside txn, upsert inside. **fix**
- **L8** no `SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE` in prod → set when `not DEBUG`. **fix**
- **L9** encrypted-field key-rotation returns ciphertext-as-plaintext → log + raise. **fix**
- **L10** CSV formula-injection surface in exports → prefix risky cells. **harden**
- **L6** invite links multi-use / not email-bound / unrevocable (no Invite model) — DESIGN
  tradeoff. Flagged for Golda: acceptable for v1, or add single-use/email-binding? → _Answer:_

### Pre-public checklist (before flipping repo to public per Q6)
- Scrub or remove this `scratch.md` (it references internal paths/org names) — it is the
  active coordination board during dev, not part of the shipped toolkit.

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
  - _Answer:_ **Rec (confirm):** org-wide **default rate + per-member override**. Simplest
    onboarding (set one rate, done) but lets you differentiate when needed; the model already
    supports both, so this costs nothing and defers the per-person conversation.
- **Q5b — Historical import source of truth on conflict:** legacy `issued_cook` table vs
  the totals spreadsheet — which wins?
  - _Answer:_ **Rec (confirm):** **`issued_cook` wins** — it's the post-approval record of
    record (the spreadsheet was a working scratchpad whose adjustments were approved *back
    into* issued_cook). Import from issued_cook, spot-check totals against the spreadsheet,
    and represent any residual spreadsheet-only delta as an explicit `OpeningBalance`
    adjustment row with a `source_note` so it's traceable.
- **Q5c — Budget policy defaults for our own org:** weekly assignable amount? self-assign cap?
  (Default seeded = unlimited / soft-warn only.)
  - _Answer:_ **Rec (confirm):** start **unlimited + no self-assign cap, soft enforcement
    (warn only)**. The team runs without budgets today; hard caps change behavior. Turn on
    observability first (the budget state is shown), then tighten once you can see real
    assignment patterns.
- **Q5d — Our instance's org slug / unit:** `whatscookin / COOK` or `linkedtrust / COOK`?
  - _Answer:_ **Rec (confirm):** **`whatscookin` / `COOK`** for the org that inherits the
    historical `issued_cook` equity — that's where the data + cap-table continuity live.
    Add `linkedtrust` as its own separate org later if/when it needs its own pie. (This is
    genuinely your call — whose equity does this record represent?)
- **Q5e — Taiga hours field:** native Taiga points, or a custom attribute? (Adapter maps
  either; question is what OUR Taiga will use.)
  - _Answer:_ **Rec (confirm):** keep **`direct_value` (the `Ncook` value tags)** for now —
    no hours field needed, matches current practice. When you move to `hours_rate`, use a
    **dedicated custom attribute `hours`**, NOT native story points (points are used for
    estimation/velocity; conflating them with billable hours is confusing). The adapter
    supports both, so this is a config flip later.

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

## DESIGN — pattern language + first application (2026-07-08, design session, branch `design/pattern-language`)
Golda's brief: `~golda/work/2026-07-08-design-language-context-prompt.md` + verbal steers (keep the logo; minimal,
quiet, small quiet buttons; content is the meat; tree motif from the logo/blog but never noisy; NEVER any animation).
- **`docs/design/pattern-language.md`** — the LinkedTrust pattern language (Alexander-style): 18 named patterns,
  grown from the tree logo (dark plum trunk, six leaf colors) + the site's hand-designed parts (warm light base,
  mono accents). Philosophy: thriving live oak; paper-and-ink ledger; quiet so the content shines.
- **`static/govkit.css`** — reference implementation: one dependency-free token sheet (CSS vars, light+dark,
  WCAG AA, zero animation). The 3 per-app CSS files (which each redefined gk-btn/gk-table in clashing greens)
  are now stubs. Pie slice colors are `.gk-cat-0..5` classes — six leaf hues from the logo, deepened + validated
  (dataviz six-check validator, both modes) replacing the Google-chart hex palette baked into pie/views.py.
- Applied to real pages: base layout (added the missing doctype; twig mark; aria-current tabs), Pie + standing
  (leaf swatches, canopy bar with gaps, branch-line traces, stat tiles), login (plain door). Drops/Votes/Committee
  inherit via the shared components.
- **Verified**: playwright walkthrough of every surface, light + dark, against the seeded demo — screenshots sent
  to Golda. Tests: no new failures vs main (the ~30 env failures fail identically on unmodified main under the
  prod .env; the 180-green record is the Compose env).
- **Deploy state:** branch pushed; the live checkout is on the branch; `collectstatic` done. ⚠ Restarting the
  demo backend service is blocked by the amebo session guard (non-amebo unit on the shared VM) — one human
  restart of that unit makes https://demos.linkedtrust.us/govkit/ show the new design. Not merged to main
  (per the brief: say so first).

## DESIGN ROUND 2 + LT SSO LIVE (2026-07-09, design session — Golda steering live)
- **Merged to main + demo deployed** (Golda's go): the pattern language is live at demos.linkedtrust.us/govkit/.
- **Canonical home moved** (Golda: "sharable"): `Cooperation-org/site-linkedtrust-us` → `design/`
  (pattern-language.md + tokens.css + README). GovKit's copies are vendored; edit canonical-first.
- **Pattern 19 · DETAILS UNFOLD** (Golda: "high level concept visible first, details if you want it"):
  onboarding now = Identity + Starting point visible; Valuation/Budget are folded one-liners with defaults
  stated. New Starting point choice: fresh vs existing project → optional initial valuation recorded as the
  founder's OpeningBalance (split later via Members → import).
- **Members first-class** (Golda: "members are the most important"): Members is a nav tab (admins);
  onboarding lands on Members to invite people.
- **LinkedTrust SSO CONFIGURED on the demo**: registered OIDC client `lt_12efb2ecc21948960033a668`
  ("GovKit (demo)") in the live provider DB (same path as Odoo/Marten/Amebo clients, replicating
  register-oidc-client.ts exactly: bcrypt-hashed secret). Creds in `.env` (0600). Verified: login page shows
  "Continue with LinkedTrust"; /start 302s to live authorize; provider accepts client + redirect_uri and
  hands off to its login. Final leg (code exchange) needs a real login — Golda to click through once.
  **After she confirms: unset GOVKIT_DEV_LOGIN and restart** (the standing lock-down note above).
- **Test-suite mystery SOLVED**: the ~30 "failures" were the prod .env leaking into pytest (BASE_PATH +
  DEBUG=0 manifest static). Under a clean env the FULL suite passes (181, including 2 new onboarding tests).
  Consider a conftest guard that forces BASE_PATH=""/DEBUG for tests — future session.
- guard.py note (amebo repo): (tmp-)govkit-* units now in the service allowlist (Golda-authorized).
- Book: "Will Work for Pie" not found on the VM or abra — asked Golda for a copy; noted we are NOT
  following it verbatim (GovKit already deliberately diverges from strict Slicing Pie).

## EARNKIT PLAYBOOK BUILD (2026-07-12, Fable/amebo session — Golda directing live)
Building `deploy/` per the SETTLED spec `~/work/7-6-2026-cohort-vm-ansible-instructions.md`:
one Ansible playbook → fresh cohort-services VM (Taiga+Odoo17+amebo+GovKit, native systemd,
no Docker, DBs on VM 100 via database_host var, LT SSO everywhere, add-team.yml).
I touch ONLY `deploy/` — no app code, no templates, no static. Design session: ignore deploy/.
Nothing gets deployed to VM 200; target is the future cohort VM (Golda creates it).

## EARNKIT LOCATION CHANGE (2026-07-12, Golda live): NOT in govkit
Golda's call supersedes the 7/6 doc: the cohort-stack playbook lives in its OWN repo
(Cooperation-org/earnkit), composing amebo+marten+odoo+taiga+govkit from source with CI/CD.
govkit stays the standalone decision-making tool. Nothing in govkit/deploy/ changes.
Fable session builds earnkit; ignore earlier note about deploy/.
