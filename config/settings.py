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
)

# Load a local .env if present (dev). In prod, real env vars win.
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(str(env_file))

# --- Core ---
SECRET_KEY = env("SECRET_KEY", default="dev-insecure-key-change-me")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    # GovKit apps
    "apps.accounts",
    "apps.orgs",
    "apps.tasksources",
    "apps.drops",
    "apps.pie",
    "apps.exports",
    "apps.votes",
    "apps.sortition",
    # --- Auth seam (uncomment once django-linkedtrust-auth is installed) ---
    # "linkedtrust_auth",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
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
