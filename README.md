# GovKit

**GovKit** is an open-source **earned-governance toolkit**. Teams earn ownership /
governance weight from **peer-reviewed, approved tasks** in their task tracker, and that
one earnings record powers everything else: equity visibility (a "pie"), work-weighted
votes, and work-weighted sortition for committees.

It is **self-hostable** (Docker Compose, no dependence on any hosted infra) and can also
be run as a **multitenant service** (one instance, many orgs).

> **Status: Milestone 1 in progress.** The foundation — data schema, multitenancy,
> Django admin, base templates + tab nav, Docker Compose — is in place. Feature logic
> (Taiga sync, drop-run review flow, pie computation, import/export, votes, sortition)
> is stubbed and being filled in. Nothing here is production-ready yet.

## Concepts

- **Org** — a tenant. Self-hosters run one; a service host runs many. Each org names its
  own unit of value ("COOK", "slices", "points", "$").
- **Drops** — the earnings engine. A steward opens a run, reviews a queue of approved
  tasks (adjusting values with a required reason), and approves it. Approved lines are
  immutable, issued equity.
- **Pie** — `Σ issued DropLines + OpeningBalances`, per member, per org. Every slice
  traces back to the work that earned it.
- **Votes** — informal, live, work-weighted meeting votes.
- **Committee** — work-weighted sortition draws (auditable: same seed reproduces the draw).

## Stack

Django 5 + Django REST Framework, PostgreSQL, server-rendered templates + HTMX. API-first:
every UI action also has a DRF endpoint.

## Quick start (Docker Compose)

```bash
cp .env.sample .env          # then edit SECRET_KEY, GOVKIT_SECRET_KEY, etc.
docker compose up --build    # brings up Postgres + web, runs migrations
```

Generate a Fernet key for `GOVKIT_SECRET_KEY` (encrypts stored tracker tokens):

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Local development

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
cp .env.sample .env
docker compose up -d db      # Postgres for dev/test

python manage.py migrate
python manage.py createsuperuser
python manage.py seed_org --slug demo --name "Demo Org" --unit points \
    --email admin@example.com --password devpass   # dev bootstrap
python manage.py runserver
```

Then visit `/` (org picker), sign in (dev login), and open `/o/demo/`.

### Tests & lint

```bash
pytest
black --check .
flake8 .
```

## Deploying behind a path prefix

Set `BASE_PATH=/govkit` (etc.) so the app serves correctly under
`example.com/govkit/`. All templates use `{% url %}` / `{% static %}`, so the prefix
applies automatically.

## Authentication

**LinkedTrust OIDC is the default login**; direct Google OAuth is the secondary option for
fully independent self-hosters. A clearly-labelled **dev-only** email+password login exists
so the app is usable before OAuth is wired — disable it with `GOVKIT_DEV_LOGIN=false` in any
real deployment.

## License

[MPL-2.0](LICENSE).
