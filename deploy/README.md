# GovKit — go-live runbook (VM 200 / dev)

Everything needed to take GovKit from "built + DB ready" to "live at
`https://demos.linkedtrust.us/govkit/`". **No credentials are in this file** — real secrets
live in `/opt/shared/repos/govkit/.env` (mode 0600, gitignored) on the dev VM (VM 200), and
the two still-missing login credentials are obtained in Step 2.

> **You do NOT need Proxmox for any of this.** The Postgres DB was already created and
> migrated from VM 200 over the network (VM 100 superuser via `~/.pgpass`). All remaining
> steps run **on VM 200** with `sudo` for systemd/nginx only.

---

## Status (2026-07-06)

**Done**
- Code: Milestone 1 + 2 complete, `main` green — **180 tests**, `check`/migrations/black/flake8 clean.
  Repo: <https://github.com/Cooperation-org/govkit> · board & open questions:
  [`scratch.md`](../scratch.md).
- **Database created + schema migrated** on VM 100: DB `govkit`, owner role `govkit_owner`
  (migrations), runtime role `govkit_user` (least-privilege; the app uses this). 23 tables.
- **`.env` written** at `/opt/shared/repos/govkit/.env` (0600) with a strong `SECRET_KEY`,
  the Fernet `GOVKIT_SECRET_KEY`, `DATABASE_URL` (runtime user), `GOVKIT_MIGRATE_DATABASE_URL`
  (owner, for future migrations), `DEBUG=0`, `BASE_PATH=/govkit`, hosts/CSRF.
- `staticfiles/` collected. Deploy artifacts staged in this `deploy/` dir.

**Remaining = this runbook:** obtain 2 login creds → install systemd + nginx → verify →
bootstrap the first org → register review date.

**Blocking for a *usable* login:** the app has no working login until Step 2 lands the
LinkedTrust OIDC client (and optionally Google). Dev-login is intentionally OFF in prod
(public URL). So: do Step 2 before expecting to sign in.

---

## Step 1 — Confirm the base is intact (on VM 200)

```bash
cd /opt/shared/repos/govkit
git pull                       # ensure latest main
source venv/bin/activate       # venv already exists; if not: python3 -m venv venv && pip install -r requirements.txt
ls -l .env                     # must exist, mode 0600 — it has the DB + app secrets
python manage.py check         # settings auto-loads .env via django-environ; must be clean
python manage.py showmigrations | grep -c '\[X\]'   # all applied (schema already migrated on VM100)
```
Manual `manage.py` commands auto-read `.env` (no `source .env` needed). For a one-off
migration against the DB as the **owner** role:
```bash
DATABASE_URL="$(grep '^GOVKIT_MIGRATE_DATABASE_URL=' .env | cut -d= -f2-)" python manage.py migrate
```

## Step 2 — Obtain the two login credentials, put them in `.env`

Editing `.env` (keep it 0600, do not commit — it is gitignored):

1. **LinkedTrust OIDC client (DEFAULT login).** Ask for a confidential client at
   <https://live.linkedtrust.us> (see the pattern in
   [`Cooperation-org/django-linkedtrust-auth`](https://github.com/Cooperation-org/django-linkedtrust-auth)):
   - redirect_uri: `https://demos.linkedtrust.us/govkit/accounts/linkedtrust/callback/`
   - scopes: `openid email profile trust`
   - Put the returned id/secret into `LINKEDTRUST_CLIENT_ID` / `LINKEDTRUST_CLIENT_SECRET`.
2. **Google OAuth client (secondary, optional but recommended).** Google Cloud Console →
   Credentials → OAuth client (Web). JS origin `https://demos.linkedtrust.us`; redirect
   `https://demos.linkedtrust.us/govkit/accounts/google/callback/`. Put into
   `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET`.

(Taiga sync — `scratch.md` Q3 — is configured **per-org in the app UI**, not in `.env`: a
steward pastes a read-only Taiga API token into the org's Task Source. Not needed to go live.)

## Step 3 — Install the systemd service (on VM 200)

```bash
sudo cp /opt/shared/repos/govkit/deploy/tmp-govkit-backend.service /etc/systemd/system/tmp-govkit-backend.service
sudo systemctl daemon-reload
sudo systemctl enable --now tmp-govkit-backend.service
sudo systemctl status tmp-govkit-backend.service --no-pager
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8062/    # expect 200/302
```
Gunicorn binds `127.0.0.1:8062` (port claimed in `cobox/vm200-ports.md`). Re-run
`python manage.py collectstatic --noinput` if static assets changed.

## Step 4 — Install the nginx proxy (on VM 200)

```bash
sudo cp /opt/shared/repos/govkit/deploy/nginx-govkit.conf /etc/nginx/app-proxies/govkit.conf
sudo nginx -t && sudo systemctl reload nginx
```
Confirm the `demos.linkedtrust.us` server block includes `/etc/nginx/app-proxies/*.conf`
(most demos already are; if not, add the include). Then:
```bash
curl -s -o /dev/null -w '%{http_code}\n' https://demos.linkedtrust.us/govkit/           # 200
curl -s -o /dev/null -w '%{http_code}\n' https://demos.linkedtrust.us/govkit/static/govkit.css  # 200
```

## Step 5 — Bootstrap the first org + admin

The onboarding wizard requires being logged in, and prod login is OIDC/Google (Step 2). Once
those work, sign in once, then create the org via the onboarding flow. If you need to seed an
initial org/admin non-interactively (e.g. before OIDC is ready, for a smoke check), use the
management command from the repo dir (creates an org + an admin membership):
```bash
python manage.py seed_org --slug <slug> --name "<Name>" --unit <unit> --email <you@domain> --password '<strong>'
```
Decide the real org slug/unit per `scratch.md` **Q5d** (`whatscookin`/`linkedtrust` + `COOK`).
Do **not** leave a password account usable in prod long-term — prefer OIDC. Dev-login stays OFF.

## Step 6 — Register + housekeeping

- The app-registry entry and the `:8062` port claim are already added in `cobox/`. Update the
  entry's **review date** and flip the DB/systemd/domain cells to "live" once Steps 3–4 pass.
- **Back up the DB creds to the vault** (passbolt): the only durable copy today is
  `/opt/shared/repos/govkit/.env` on VM 200 — copy `govkit_owner` / `govkit_user` passwords
  into the team vault so a lost `.env` doesn't orphan the DB.
- Before making the repo **public** (`scratch.md` Q6): scrub/remove `scratch.md` (it references
  internal paths). See its "Pre-public checklist".

## Rollback
```bash
sudo systemctl disable --now tmp-govkit-backend.service
sudo rm /etc/nginx/app-proxies/govkit.conf && sudo nginx -t && sudo systemctl reload nginx
```
The DB can stay (empty/low-risk) or be dropped from VM 200 with the `cobox` superuser:
`psql "host=10.0.0.100 user=cobox dbname=postgres" -c 'DROP DATABASE govkit'` (plus the two roles).

---

## Open questions / deviations (from `scratch.md`, for the executor to resolve)
- **Q2/Q4** creds (Step 2) · **Q5d** org slug+unit (Step 5) · **Q3** Taiga token (per-org, post-launch).
- **D1**: LinkedTrust OIDC is implemented in-app (session-based) rather than via the pip
  package — same protocol/IdP. **L6**: invite links are multi-use bearer tokens. Both flagged
  in `scratch.md` for accept/change.
