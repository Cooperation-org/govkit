"""Test settings: sqlite in-memory, no external services.

Used by pytest (see pytest.ini). Production/dev settings stay in settings.py.
"""

from .settings import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Neutralize deployment-specific values a local .env may set; tests assume
# no path prefix (test_base_path.py overrides these itself when it needs one).
BASE_PATH = ""
FORCE_SCRIPT_NAME = None
STATIC_URL = "/static/"
SESSION_COOKIE_PATH = "/"
CSRF_COOKIE_PATH = "/"

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
