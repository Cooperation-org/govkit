# Self-hosting GovKit with Docker Compose

This guide takes a fresh checkout to a running org: configure the environment, bring up the
stack, create your first org, connect a Taiga task source, run a drop, and view the pie.

Every command and variable below is checked against the repository. If something here does
not match the code you have, trust the code and open an issue.

> Docker Compose is the supported path for self-hosting and for local development. It runs
> the web app plus a bundled PostgreSQL. Point `DATABASE_URL` at an external database if you
> prefer to manage Postgres yourself.

## 1. Prerequisites

- **Docker** with the **Compose** plugin (`docker compose version` should work).
- **Python 3** on the host — only needed to generate a couple of keys below; it is not
  required to run the app (that happens inside the container).
- A **Taiga** instance you can reach over its REST API, plus an API token, if you want to
  sync real tasks. You can create an org and explore the UI without one.

## 2. Clone

```bash
git clone <your-fork-or-mirror-of-govkit> govkit
cd govkit
```

## 3. Configure the environment

Copy the sample and fill it in:

```bash
cp .env.sample .env
```

`.env.sample` is the authoritative list of variables. Each one, and what it means:

### Core Django

| Variable | Meaning |
|---|---|
| `DEBUG` | `true` for local/dev, **`false` in any real deployment**. In `DEBUG` mode static files are served unhashed and the dev login seam can be used. |
| `SECRET_KEY` | Django's secret key. Set a long random string. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(50))"`. |
| `ALLOWED_HOSTS` | Comma-separated hostnames the app will answer to (e.g. `localhost,127.0.0.1,govkit.example.org`). |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated origins trusted for unsafe requests. Needed when running behind an HTTPS reverse proxy, e.g. `https://govkit.example.org`. |
| `BASE_PATH` | Path prefix when serving under a sub-path (e.g. `/govkit`). Leave **empty** for local dev or when serving at a domain root. See [Serving behind a path prefix](#8-serving-behind-a-path-prefix). |

### Database

| Variable | Meaning |
|---|---|
| `DATABASE_URL` | Postgres connection URL. **Compose overrides this automatically** to point at the bundled `db` service, so the value in `.env` is used for non-Compose runs and for the bundled Postgres credentials. Default: `postgres://govkit:govkit@db:5432/govkit`. Set it to your own host to use an external database. |

The Compose file also reads `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` (all
default to `govkit`) and the published ports `DB_PORT` (default `5432`) and `WEB_PORT`
(default `8000`). Add those to `.env` only if you need to change them — for example set
`DB_PORT=5433` if the host already runs Postgres on `5432`.

### Task-source token encryption (required)

| Variable | Meaning |
|---|---|
| `GOVKIT_SECRET_KEY` | A **Fernet** key used to encrypt task-source API tokens at rest. It must be set before you save any Taiga token — GovKit raises rather than store a plaintext token. |

Generate it with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Paste the output as the value of `GOVKIT_SECRET_KEY`. Keep it stable — rotating it makes
previously stored tokens unreadable.

### LinkedTrust OIDC (default login)

| Variable | Meaning |
|---|---|
| `LINKEDTRUST_URL` | The LinkedTrust OIDC issuer, e.g. `https://live.linkedtrust.us`. |
| `LINKEDTRUST_CLIENT_ID` / `LINKEDTRUST_CLIENT_SECRET` | The confidential OIDC client registered for your deployment. |
| `LINKEDTRUST_SCOPES` | OIDC scopes; default `openid email profile trust`. |
| `LINKEDTRUST_FRONTEND_URL` / `LINKEDTRUST_FRONTEND_CALLBACK` | Front-end URL and callback path used by the OIDC flow. |

Register your app's callback — `<base>/accounts/linkedtrust/callback/` — as the client's
redirect URI (include your `BASE_PATH` in `<base>` if you use one).

### Google OAuth (secondary login)

| Variable | Meaning |
|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` | Google OAuth client credentials, for self-hosters who do not run a LinkedTrust IdP. |

Register `<base>/accounts/google/callback/` as the Google client's redirect URI.

### Dev-only login seam

| Variable | Meaning |
|---|---|
| `GOVKIT_DEV_LOGIN` | `true` enables a clearly-labelled password login so the app is usable before OAuth is wired. **Set it to `false` (or leave it unset) in any real deployment.** The dev-login route returns 404 when this is off. |

## 4. Bring up the stack

```bash
docker compose up --build
```

This builds the web image, starts PostgreSQL, waits for it to be healthy, **applies
migrations automatically**, and serves the app via gunicorn on port `8000` (or `WEB_PORT`).

If you want to sanity-check the Compose file before starting, `docker compose config`
renders the fully-resolved configuration.

### Running migrations manually

The container entrypoint runs `migrate` on start, so you normally do not need to. To run
them by hand (for example against an external database, or after pulling new code with the
stack already up):

```bash
docker compose run --rm web python manage.py migrate
```

## 5. Create the first org

Two ways to create an org:

**A. Onboarding wizard (recommended).** You need a user to sign in as first. With OAuth
configured, sign in through your provider. For a quick local start with the dev seam
(`GOVKIT_DEV_LOGIN=true`), you can bootstrap an admin user and org in one command:

```bash
docker compose run --rm web python manage.py seed_org \
    --slug demo --name "Demo Org" --unit points \
    --email admin@example.com --password devpass
```

`seed_org` creates an `Org`, its `ValuationConfig`, and one **admin** membership. It is a
dev/verification bootstrap only — it does not seed domain data (opening balances come
through the real import feature, below).

Then visit the site root `/`, sign in, and open the org at `/o/demo/`.

**B. In the app.** Sign in, then go to `/onboarding/`. The wizard captures, in one flow:

- **Organization name** and **URL slug** (the slug appears in every org URL);
- **Value unit** — what this org calls a unit of earned value (`COOK`, `points`, `$`, …);
- **Org-wide default hourly rate** (optional; members can override it);
- **Valuation mode** — `Hours × rate` or `Direct value tags` (see below);
- **At-risk multipliers** (non-cash / cash; default `1.0` = off — set `2.0` / `4.0` to match Slicing Pie);
- **Weight window** — `All time` or `Trailing 12 months` (used for vote / sortition weighting);
- **Assignment-budget policy** — period, assignable amount (blank = unlimited), self-assign cap (blank = none), and soft (warn) vs hard (block) enforcement.

Submitting creates the org and makes you its admin, and lands you on the org dashboard.

You can invite others from `/o/<slug>/members/` (email, role — `admin` / `steward` /
`member` — and an optional per-member rate override).

## 6. Connect a Taiga task source

The Taiga adapter talks to Taiga's **REST API with an auth token** — it never touches the
Taiga database. Configure a task source for the org (via Django admin at `/admin/`, or the
tasksources API) with:

- **base URL** — your Taiga REST API base (e.g. `https://taiga.example.org`);
- **API token** — encrypted at rest with your `GOVKIT_SECRET_KEY`;
- **project selector** — comma-separated Taiga project slugs and/or ids to pull;
- **done statuses** — status slugs that count as eligible; defaults to `done`, `archived`, `historical`;
- the **field mapping** for your valuation mode (below).

GovKit supports two valuation modes, chosen per org in the valuation config:

### `hours_rate` mode

Value is `rate × hours − cash`, times the at-risk multipliers. Map:

- **hours field** — the name of the Taiga custom attribute carrying hours. The special
  values `points` / `total_points` mean "use Taiga's native story points" instead of a
  custom attribute.
- **cash field** (optional) — the custom attribute carrying cash already paid on a task.

A member's `rate` is their per-member `hourly_rate`, falling back to the org default. A
missing rate values that task at 0, so a steward corrects it with an adjustment in the drop
run rather than it silently vanishing.

### `direct_value` mode

Value is read straight off task tags. Set a **value tag pattern** — a case-insensitive
regex whose first group is the numeric value. For a `"COOK"` unit that is:

```
(\d+)\s*cook
```

so a tag like `5 cook` or `5cook` yields a value of 5; multiple matching tags are summed.
There is deliberately **no default pattern** — a bare `(\d+)` would match any number in any
tag (a `3 priority` tag would read as value 3). Until you set a unit-specific pattern,
direct-value tasks surface in the **missing-value queue** rather than being miscounted.

### Syncing

Pull the org's tasks from Taiga:

```bash
docker compose run --rm web python manage.py sync_tasksource demo
```

Sync is idempotent — it upserts `TrackedTask` rows keyed on `(org, source, external_id)`,
so re-running updates rather than duplicates. A scheduler should call
`apps.tasksources.services.refresh_all` (all orgs) rather than shelling out per org. You can
also trigger a sync from the org's Tasks page at `/o/<slug>/tasks/`.

## 7. Run a drop, then view the pie

1. **Open a run.** As a steward or admin, go to `/o/<slug>/drops/` and open a run. GovKit
   gathers eligible done, assigned tasks not already part of a drop line, groups them by
   member, and computes each line's value from the valuation config.
2. **Review by task.** The review screen shows lines grouped by member plus a
   **missing-value queue** — the tasks that need a value. Under-claiming is the failure mode
   this step exists to correct.
3. **Adjust with a reason.** Any non-zero adjustment **requires a reason** and records who
   adjusted it and when. `final_value = computed_value + adjustment`.
4. **Approve.** Approving flips the run to `approved` and stamps who approved it. Its lines
   become **issued** and immutable — further edits are refused.
5. **View the pie** at `/o/<slug>/pie/`: each member's share of the org total, with
   drill-down from any slice to the exact drop lines and tasks (and opening balances) behind
   it. Each member sees their own realized-vs-pending standing at `/o/<slug>/pie/me/`.

The pie is `Σ final_value of lines in approved runs + Σ opening balances`, per member.

## 8. Opening-balance import and export

### Import (start an org with existing equity)

If your team already has equity to carry in, import it as opening balances — there is no
separate "demo data" path; import *is* that feature. On the org's **Import / Export** page
(`/o/<slug>/exports/`, admin/steward only) upload a CSV:

| Column | Required | Meaning |
|---|---|---|
| `value` | yes | Opening equity for the member, in the org's unit (a decimal). |
| `member_email` | one of\* | Matched to the member's login email. |
| `taiga_user_id` | one of\* | Matched to the member's mapped Taiga user id. |
| `source_note` | no | Free-text provenance (e.g. `issued history 2026-07`). |

\* Each row needs at least one identifier. An identifier that matches no member is reported
as a bad row and **never creates a member** — identity mapping is explicit, never inferred.
Import mode is `replace` (idempotent per member) or `append`. Bad rows are reported;
valid rows still import; every run is recorded as an audit batch.

### Export (get equity back out)

From the same page, download an export. Equity leaves GovKit only through these adapters:

- **Generic CSV** — one row per member: emails, issued value, opening balance, total, share, unit.
- **Slicing Pie** — one row per contribution (each issued drop line and each opening
  balance), surfacing hours / rate / cash offset and the org's at-risk multipliers as
  columns. GovKit is an ally of Slicing Pie, not a competitor.

Download URLs are `/o/<slug>/exports/export/generic.csv` and
`/o/<slug>/exports/export/slicing_pie.csv`.

## 9. Serving behind a path prefix

To serve under a sub-path such as `example.org/govkit/`, set:

```
BASE_PATH=/govkit
```

GovKit applies this as Django's `FORCE_SCRIPT_NAME` and prefixes static, session, and CSRF
cookie paths accordingly. Every template uses `{% url %}` / `{% static %}`, so the prefix
flows through automatically — no template changes needed. Behind a TLS-terminating reverse
proxy, also set `CSRF_TRUSTED_ORIGINS` to your external HTTPS origin and remember to include
`BASE_PATH` in each OAuth provider's registered redirect URI.

## 10. Local development (without the bundled web container)

To iterate on the code with the app on the host and only Postgres in Compose:

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
cp .env.sample .env
docker compose up -d db          # just Postgres (set DB_PORT if 5432 is taken)

python manage.py migrate
python manage.py seed_org --slug demo --name "Demo" --unit points \
    --email you@example.com --password devpass
python manage.py runserver
```

Keep the checks green before committing:

```bash
pytest
black --check .
flake8 .
```
