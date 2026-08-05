# GovKit — map of the repo

What lives where. Read this first to find a thing; read `README.md` for what GovKit is,
`docs/BOUNDARIES.md` before adding a table or a page, `CLAUDE.md` for the deploy rule.

Repo: `Cooperation-org/govkit`, checked out at `/opt/shared/repos/govkit`.
Django 5 + DRF, Postgres on VM 100. Push to `main` = deploy to `dash.workers.vc`
(`.github/workflows/deploy-to-cohort.yml` runs migrate + collectstatic + restart).

## Apps (`apps/`)

| App | Owns | Key models |
|---|---|---|
| `accounts` | people, login | `User`, `ProfileLink` |
| `orgs` | orgs, members, invites, cohorts | `Org`, `Membership`, `Invite`, `Cohort`, `ValuationConfig`, `OrgPicture/Link/Quote/Post`, `ChecklistEvent` |
| `commons` | ideas + interest, mail, pictures | `Idea`, `VentureInterest`, `IdeaInterest` |
| `tasksources` | pulling tasks from trackers | `TaskSourceConfig`, `TrackedTask` |
| `drops` | the review queue that issues equity | `DropRun`, `DropLine` |
| `pie` | who holds what share | `PieLockVote` (shares are computed from drop lines) |
| `votes` | work-weighted votes | `Vote`, `Ballot` |
| `sortition` | seeded committee draws | `SortitionDraw` |
| `projects` | projects, deals, splits, payouts | `Project`, `ProjectLink`, `Deal`, `Split`, `Payout` |
| `exports` | imports/exports (Slicing Pie etc.) | `ImportBatch` |

Each app: `models.py`, `views.py` (HTML), `api.py` (DRF viewsets), `urls.py`,
`services.py` where there is real logic, `templates/<app>/`.
Feature apps add their own routes/viewsets; never edit another app's routing.

## Routes (`config/urls.py`)

```
/                            landing / org picker
/onboarding/                 org-creation wizard
/invites/<code>/accept/      the invite door  ← public invite page
/teams/                      all teams in the cohort
/mentors/                    cohort mentors + their booking links
/cohorts/<slug>/             cohort progress
/o/<org>/                    org dashboard
/o/<org>/{drops,pie,votes,committee,exports,tasks,projects}/
/o/<org>/{about,settings,members,open}/
/accounts/...                login (LinkedTrust OIDC default, Google secondary)
/api/v1/<app>/...            one DRF router per app
/admin/
```

## Invites

Model `Invite` in `apps/orgs/models.py` (~line 502). Three orthogonal fields:

- **audience** — who it addresses, and the words the door uses:
  `mentor`, `funder`, `founder`, `supporter`. (`advisor`/`partner` retired; old rows
  are translated on display.)
- **kind** — where accepting lands you: `org` (join this org), `pool` (applicant pool,
  no membership), `byov` (founder brings own venture → a NEW org, they are admin).
- **status** — `created → committed → accepted`, or `revoked`.

Code: `apps/orgs/invites.py` (minting), `apps/orgs/views.py:accept_invite`,
mint from CLI with `manage.py mint_invite`.
**Copy the invitee reads: `templates/orgs/invite_door.html`** — headline, the
audience line, the inviter's drafted statement, the sign-in buttons.

Mentors are not a separate role system: a mentor is a person with a `Membership`
in the orgs they mentor, plus a booking link. `/mentors/` is built in
`apps/orgs/doorway.py:mentors()`.

## Cohort / doorway

- `apps/orgs/cohorts.py` — cohort model helpers, progress page.
- `apps/orgs/doorway.py` — the public wall: teams, mentors, what shows to whom.
- `apps/orgs/sitepull.py` — pulling profile content from a team's own site.
- `apps/orgs/amebo.py` — the amebo seam.
- `apps/orgs/embed_auth.py` + `static/embed/govkit.js` — embeddable widgets, shared
  session with the cohort site.
- `PLAN-cohort-dash.md` — the cohort dashboard plan.

## Templates

`templates/base.html`, `templates/orgs/` (landing, dashboard, invite_door, members,
mentors, all_teams, cohort_progress, about, onboarding, settings),
`templates/accounts/` (login, profile), `templates/commons/`, plus per-app
`apps/<app>/templates/<app>/`.

## Docs in the repo

`README.md`, `CLAUDE.md`, `AGENTS.md`, `UX_PRINCIPLES.md`, `docs/BOUNDARIES.md`,
`docs/self-hosting.md`, `docs/governance-practices.md`, `docs/design/pattern-language.md`,
`PLAN-cohort-dash.md`, `scratch.md` (session coordination board — read the tail first).

## Ops

`deploy/` — nginx conf, `tmp-govkit-backend.service`, autodrop timer.
Management commands: `mint_invite`, `seed_org`, `seed_demo` (orgs),
`sync_tasksource` (tasksources), `autodrop` (drops).
Tests in `tests/`, run with `pytest`.
