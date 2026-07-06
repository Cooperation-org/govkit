# GovKit

**GovKit** is an open-source, multitenant **earned-governance toolkit**. A team earns
ownership and governance weight from **peer-reviewed, approved tasks** in the task tracker
it already uses. That single earnings record then powers everything else:

- **the pie** — who holds what share of earned equity, and why;
- **work-weighted votes** — informal, live meeting decisions weighted by earned contribution;
- **work-weighted sortition** — seeded, auditable committee draws.

One record, three uses. A share is never a number someone typed into a spreadsheet — it
traces back to the specific work that earned it.

## How it works

Value lives on the task, in the tracker, set when the task is created or reviewed. When a
task is approved as done, GovKit picks it up. Periodically a steward opens a **drop run**:
a review queue of approved tasks, where values can be adjusted (with a required reason)
before the run is approved. Approved lines become **issued** equity — immutable, and the
input to the pie, the votes, and the draws.

### The value unit is configuration, not architecture

Each org names its own unit of earned value — `"COOK"`, `"slices"`, `"points"`, `"$"`.
GovKit assumes nothing about it. There is no token type baked into the code; the unit is a
per-org setting.

### How this differs from Slicing Pie (we are allies, not competitors)

Slicing Pie values contribution as self-reported hours × rate. GovKit values
**peer-reviewed tasks**: the value is agreed when the task is created or valued, and the
earning happens when the task is approved as done. The two models stay compatible — Slicing
Pie's hours × rate, cash offsets, and at-risk multipliers are all supported as
configuration, and **Slicing Pie is a supported export target**.

## Status

**Milestone 1 is complete and integrated.** That covers:

- OAuth login (LinkedTrust OIDC default, Google secondary), org onboarding, invites, roles;
- the Taiga task-source adapter (REST API), with both valuation modes and a missing-value queue;
- the drop engine — open a run, review by task, adjust with a reason, approve into immutable issued lines;
- the pie — live shares with drill-down from any slice to the tasks behind it, plus a personal-standing page;
- opening-balance import and generic-CSV / Slicing-Pie export.

**Milestone 2 is in progress.** The data models for **votes** and **sortition** exist;
their pages are still placeholders while the tally and draw logic are built. This
documentation is part of Milestone 2.

Formal **elections** are intentionally out of scope — those stay in the team's existing
email-based ElectionRunner. GovKit does not build voting-by-email.

## Getting started

Self-hosting via Docker Compose is covered step by step in the
**[self-hosting guide](docs/self-hosting.md)** — clone, configure `.env`, bring up
Postgres + web, create your first org, connect a Taiga source, run a drop, and view the
pie. For how the governance mechanisms work as a process, see
**[governance practices](docs/governance-practices.md)**.

A minimal first run:

```bash
cp .env.sample .env          # then set SECRET_KEY and GOVKIT_SECRET_KEY (see the guide)
docker compose up --build    # brings up Postgres + web and applies migrations
```

## Stack

- **Django 5** + **Django REST Framework** — API-first: every UI action also has a DRF endpoint.
- **PostgreSQL** — the system of record.
- Server-rendered Django templates with minimal JavaScript.
- `psycopg` 3, `gunicorn`, `whitenoise`, `cryptography` (Fernet, for encrypting stored tracker tokens).

## Authentication

**LinkedTrust OIDC is the default login.** Direct **Google OAuth** is the secondary option
so a fully independent self-hoster is not forced to run a LinkedTrust identity provider.
Email + password on its own is not a supported production login; a clearly-labelled
**dev-only** password login exists so the app is usable before OAuth is wired, and is off
unless `GOVKIT_DEV_LOGIN=true`. Configure the providers with the `LINKEDTRUST_*` and
`GOOGLE_OAUTH_*` variables in `.env` (see the self-hosting guide).

## A few points of clarity

- **Not web3.** No wallets, no tokens-first design, no new ledger layer.
- **Postgres is the system of record.** Earnings, drops, and shares live in the database.
- **Equity leaves only through exports.** It never leaves GovKit except via an export
  adapter (generic CSV and Slicing Pie today; more can be added).
- **Multitenant from day one.** Every domain table is scoped to an org. A self-hoster
  simply runs one org; a service host runs many.

## License

[MPL-2.0](LICENSE).

## Contributing

GovKit is meant to be run by teams independent of its authors, and contributions are
welcome. A good change is often not code — a clearer doc, a governance pattern, a new
export or task-source adapter. Adapters are the designed extension points:

- **Task sources** — subclass `TaskSourceAdapter` (`apps/tasksources/adapters.py`) and
  register it; Taiga is the first, GitHub Issues / Linear can follow the same interface.
- **Exports** — subclass `EquityExport` (`apps/exports/exporters.py`), implement
  `fieldnames()` + `rows(org)`, and add one registry entry.

Please keep changes org-scoped (never query a domain model without `.for_org(org)`), keep
every UI action backed by a DRF endpoint, and keep `pytest`, `black --check .`, and
`flake8 .` green. No person names in committed files, and no secrets in git — `.env.sample`
holds placeholders only.
