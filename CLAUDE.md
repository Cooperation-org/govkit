# CLAUDE.md

Guidance for any agent working in this repository.

**[UX_PRINCIPLES.md](UX_PRINCIPLES.md) — read before touching anything a person looks at.** Show don't tell; everything actionable; if I can see it I can edit it; omit needless words. Same file in `amebo` and `workers.vc`.

**[docs/BOUNDARIES.md](docs/BOUNDARIES.md) — read before adding a table, a page, or a
new place to store something.** What GovKit owns, what it only references, the code
layout, and the list of things to ASK about rather than decide alone. Peer doc:
`../amebo/docs/BOUNDARIES.md`, and `../workers.vc/docs/BOUNDARIES.md`.

**[README.md](README.md)** — what GovKit is and how it fits the rest of the stack.
**[PLAN-cohort-dash.md](PLAN-cohort-dash.md)** — the cohort dashboard plan.
**[scratch.md](scratch.md)** — session coordination board. Read the tail before starting; announce what you pick up.

## PUSHING TO MAIN IS DEPLOYING

There is no separate deploy step and no staging. A push to `main` fires
`.github/workflows/deploy-to-cohort.yml`, which runs `update-govkit` on the
cohort VM: reset to `origin/main`, install requirements, **`manage.py migrate
--noinput`**, `collectstatic`, restart the service. Roughly fifteen seconds
later it is live at `dash.workers.vc` for every team.

So: migrations ship and run themselves — never tell anyone a migration is
pending or ask them to run one. And a half-finished push is in front of people,
not sitting on a branch. Tests green and the change coherent before you push.

`gh run list --workflow=deploy-to-cohort.yml` shows whether the last push landed.
Deploy scripts and roles live in `../earnkit`, not here.

Shared-VM rules (commit to main and push as you go, never `git stash`, never
`git worktree`, best practices always, no hacks) live in `~/CLAUDE.md`.
