"""
Django settings for GovKit.

All configuration comes from the environment (12-factor). See .env.sample for the
full set of variables. Nothing here hardcodes an ID, path, URL, or secret.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CSRF_TRUSTED_ORIGINS=(list, []),
    BASE_PATH=(str, ""),
    GOVKIT_SECRET_KEY=(str, ""),
    GOVKIT_DEV_LOGIN=(bool, False),
    # Auth seams (populated by the auth agent / deploy env).
    LINKEDTRUST_URL=(str, ""),
    LINKEDTRUST_CLIENT_ID=(str, ""),
    LINKEDTRUST_CLIENT_SECRET=(str, ""),
    LINKEDTRUST_SCOPES=(str, "openid email profile trust"),
    LINKEDTRUST_FRONTEND_URL=(str, ""),
    LINKEDTRUST_FRONTEND_CALLBACK=(str, "/oauth/callback"),
    GOOGLE_OAUTH_CLIENT_ID=(str, ""),
    GOOGLE_OAUTH_CLIENT_SECRET=(str, ""),
    GOVKIT_S2S_TOKEN=(str, ""),
    DOORWAY_BASE_URL=(str, "https://linkedtrust.us/earnedgov/i/"),
    AMEBO_BASE_URL=(str, ""),
    AMEBO_S2S_TOKEN=(str, ""),
    CORS_ALLOWED_ORIGINS=(list, []),
    LOGIN_NEXT_ALLOWED_HOSTS=(list, []),
    GOVKIT_OPEN_TASKS_CACHE_SECONDS=(int, 60),
    COHORT_NAV_SRC=(str, ""),
    COHORT_FRONT_DOOR=(str, ""),
    COHORT_POOL_LANDING=(str, ""),
    PUBLIC_BASE_URL=(str, ""),
    # Where the public "About <org>" stub sends a non-member's "Request to join"
    # (the cohort's apply/main page, e.g. https://workers.vc/). Falls back to the
    # pool landing, then GovKit's own landing.
    ORG_APPLY_URL=(str, ""),
    # The accelerator org's slug (e.g. "vc"). Its admins get the cross-org
    # "All teams" oversight page. Empty => only superusers see it.
    ACCELERATOR_ORG_SLUG=(str, ""),
)

# Load a local .env if present (dev). In prod, real env vars win.
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(str(env_file))

# --- Core ---
_INSECURE_SECRET_KEY_DEFAULT = "dev-insecure-key-change-me"
SECRET_KEY = env("SECRET_KEY", default=_INSECURE_SECRET_KEY_DEFAULT)
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")

# --- CORS (cohort dash) ---
# The workers.vc dash reads the JSON API from the browser with the member's own
# session cookie (same-site under one registrable domain, so SameSite=Lax cookies
# ride along; only these response headers are needed). API paths only — HTML
# pages are never served cross-origin.
CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")

# The cohort's thin cross-app menu (a <cohort-nav> script the doorway serves);
# empty (default) mounts nothing — self-hosted GovKit stays standalone.
COHORT_NAV_SRC = env("COHORT_NAV_SRC")

# Browser-facing base for URLs handed to OTHER SERVERS to relay (S2S invite
# payloads): loopback callers must never leak http://127.0.0.1 links to real
# invitees. Empty falls back to the request host (fine for browser requests).
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL").rstrip("/")
ORG_APPLY_URL = env("ORG_APPLY_URL")
ACCELERATOR_ORG_SLUG = env("ACCELERATOR_ORG_SLUG")
CORS_ALLOW_CREDENTIALS = True
CORS_URLS_REGEX = r"^/api/"
# The dash's one write (checklist toggle) authenticates with the session cookie
# plus this preflight-gated header instead of a CSRF token (see api.py).
from corsheaders.defaults import default_headers as _cors_default_headers  # noqa: E402

CORS_ALLOW_HEADERS = [*_cors_default_headers, "x-govkit-embed"]

# H1: never run production on the insecure dev default. Invite tokens are signed with
# SECRET_KEY (django.core.signing), so a forgotten prod key = forgeable admin invites.
# Fail loudly at startup rather than silently accept forgeable signatures.
if not DEBUG and (not SECRET_KEY or SECRET_KEY == _INSECURE_SECRET_KEY_DEFAULT):
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "SECRET_KEY is unset or the insecure development default while DEBUG is False. "
        "Set a strong, unique SECRET_KEY in the environment before running in production "
        "(invite tokens are signed with it — a default key makes them forgeable)."
    )

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    # GovKit apps
    "apps.accounts",
    "apps.orgs",
    "apps.tasksources",
    "apps.drops",
    "apps.pie",
    "apps.exports",
    "apps.votes",
    "apps.sortition",
    "apps.projects",
    "apps.commons",
    # --- Auth seam (uncomment once django-linkedtrust-auth is installed) ---
    # "linkedtrust_auth",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    # CORS must run before CommonMiddleware so preflights short-circuit.
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Resolves request.org / request.membership for /o/<org_slug>/ routes.
    "apps.orgs.middleware.OrgContextMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.orgs.context_processors.nav",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- Database ---
DATABASES = {
    "default": env.db("DATABASE_URL", default="postgres://govkit:govkit@localhost:5432/govkit"),
}

# --- Auth ---
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "orgs:landing"
LOGOUT_REDIRECT_URL = "orgs:landing"

# --- i18n ---
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Static files / base-path support ---
# The app can be deployed behind a path prefix (e.g. example.com/govkit/). BASE_PATH
# drives FORCE_SCRIPT_NAME so every {% url %} / {% static %} gets the prefix.
BASE_PATH = env("BASE_PATH").rstrip("/")

if BASE_PATH:
    FORCE_SCRIPT_NAME = BASE_PATH
    STATIC_URL = f"{BASE_PATH}/static/"
    SESSION_COOKIE_PATH = f"{BASE_PATH}/"
    CSRF_COOKIE_PATH = f"{BASE_PATH}/"
else:
    STATIC_URL = "static/"

STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
# In DEBUG (dev + tests) use plain storage so templates render without a collectstatic
# manifest. In production use whitenoise's hashed+compressed manifest storage (run
# `collectstatic` first — the Dockerfile and CI both do).
_staticfiles_backend = (
    "django.contrib.staticfiles.storage.StaticFilesStorage"
    if DEBUG
    else "whitenoise.storage.CompressedManifestStaticFilesStorage"
)
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": _staticfiles_backend},
}

# Behind a reverse proxy terminating TLS.
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# L8: mark cookies secure in production (HTTPS-only). Left False in dev so local http works.
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- DRF ---
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}

# --- GovKit-specific config ---
# Fernet key used to encrypt task-source API tokens at rest. Required before any
# TaskSourceConfig token is saved; absence raises at save time (never silently plaintext).
GOVKIT_SECRET_KEY = env("GOVKIT_SECRET_KEY")

# Dev-only password login seam. Clearly not for production.
GOVKIT_DEV_LOGIN = env("GOVKIT_DEV_LOGIN")

# Server-side cache TTL (seconds) for the open-tasks proxy
# (GET /api/v1/tasksources/orgs/<slug>/tasks/open/ — a live Taiga fetch).
GOVKIT_OPEN_TASKS_CACHE_SECONDS = env("GOVKIT_OPEN_TASKS_CACHE_SECONDS")

# Hosts (hostnames, not origins) that a post-login ?next= redirect may point at as an
# absolute https URL — e.g. the cohort dash on workers.vc. Relative-path next values are
# always allowed; empty (default) rejects every absolute next (the pre-existing behavior).
LOGIN_NEXT_ALLOWED_HOSTS = env("LOGIN_NEXT_ALLOWED_HOSTS")

# The cohort dash is THE front door for members (workers.vc apex); GovKit's own dashboard
# is a menu item there. When set, completing an invite join lands the new member on this
# URL template with {org_slug} substituted, instead of GovKit's org dashboard. Unset
# (default) keeps today's behavior. Validated here so a typo fails at startup, not as a
# broken redirect. Cohort value: https://workers.vc/dash/{org_slug}/connect/
COHORT_FRONT_DOOR = env("COHORT_FRONT_DOOR")
if COHORT_FRONT_DOOR:
    from django.core.exceptions import ImproperlyConfigured

    try:
        _front_door_probe = COHORT_FRONT_DOOR.format(org_slug="probe")
    except (KeyError, IndexError, ValueError):
        _front_door_probe = None  # stray placeholder or unbalanced braces
    if (
        _front_door_probe is None
        or "{org_slug}" not in COHORT_FRONT_DOOR
        or not COHORT_FRONT_DOOR.startswith("https://")
    ):
        raise ImproperlyConfigured(
            "COHORT_FRONT_DOOR must be an https URL template containing {org_slug} "
            "(e.g. https://workers.vc/dash/{org_slug}/connect/); got "
            f"{COHORT_FRONT_DOOR!r}."
        )

# Where a POOL-invite accept lands (the person joined no org, so neither org
# dashboard nor COHORT_FRONT_DOOR applies). A plain https URL, no template. Unset
# (default) falls back to GovKit's own landing page.
COHORT_POOL_LANDING = env("COHORT_POOL_LANDING")
if COHORT_POOL_LANDING and not COHORT_POOL_LANDING.startswith("https://"):
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        f"COHORT_POOL_LANDING must be an https URL; got {COHORT_POOL_LANDING!r}."
    )

# --- LinkedTrust OIDC (default login) — read by django-linkedtrust-auth ---
LINKEDTRUST_URL = env("LINKEDTRUST_URL")
LINKEDTRUST_CLIENT_ID = env("LINKEDTRUST_CLIENT_ID")
LINKEDTRUST_CLIENT_SECRET = env("LINKEDTRUST_CLIENT_SECRET")
LINKEDTRUST_SCOPES = env("LINKEDTRUST_SCOPES")
LINKEDTRUST_FRONTEND_URL = env("LINKEDTRUST_FRONTEND_URL")
LINKEDTRUST_FRONTEND_CALLBACK = env("LINKEDTRUST_FRONTEND_CALLBACK")
# Points django-linkedtrust-auth at GovKit's user upsert handler.
LINKEDTRUST_USER_HANDLER = "apps.accounts.auth_handlers.get_or_create_user"

# --- Google OAuth (secondary login) ---
GOOGLE_OAUTH_CLIENT_ID = env("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_OAUTH_CLIENT_SECRET = env("GOOGLE_OAUTH_CLIENT_SECRET")

# --- Doorway invites (magic-link contract, scratch.md) ---
# Shared secret for the doorway's server-to-server invite API (GET invite detail,
# POST committed). Empty (default) disables those endpoints entirely: every call 401s.
GOVKIT_S2S_TOKEN = env("GOVKIT_S2S_TOKEN")

# Base URL of the public doorway page for two-step invites; the doorway magic link is
# f"{DOORWAY_BASE_URL}{invite.code}/". Set empty to hide the doorway option in the
# invite mint UI (all invites then link straight to the accept page).
DOORWAY_BASE_URL = env("DOORWAY_BASE_URL")

# --- Amebo team registry (server-to-server) ---
# amebo is the operational system-of-record for teams; accepted memberships are
# reported to POST {AMEBO_BASE_URL}/api/orgs/provision so the person gets provisioned
# across the team's tools. Either value empty (default) disables reporting entirely
# (apps.orgs.amebo.provision_membership becomes a no-op).
AMEBO_BASE_URL = env("AMEBO_BASE_URL")
AMEBO_S2S_TOKEN = env("AMEBO_S2S_TOKEN")
