"""H1: production must refuse the insecure default SECRET_KEY.

Invite tokens are signed with SECRET_KEY (django.core.signing); a forgotten prod key makes
admin invites forgeable, so importing settings with DEBUG=False + the default (or empty) key
must raise ImproperlyConfigured. We import the settings module in a clean subprocess so the
already-configured test process settings are untouched.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Import the settings module directly; the H1 guard runs at module import time.
_SNIPPET = "import config.settings  # noqa: F401\nprint('IMPORT_OK')\n"


def _run(env_overrides):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(REPO_ROOT),
    }
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", _SNIPPET],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def test_prod_with_default_secret_key_raises():
    result = _run({"DEBUG": "False", "SECRET_KEY": "dev-insecure-key-change-me"})
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "SECRET_KEY" in result.stderr
    assert "IMPORT_OK" not in result.stdout


def test_prod_with_empty_secret_key_raises():
    result = _run({"DEBUG": "False", "SECRET_KEY": ""})
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr


def test_prod_with_real_secret_key_starts():
    result = _run({"DEBUG": "False", "SECRET_KEY": "a-strong-unique-production-secret-key"})
    assert result.returncode == 0, result.stderr
    assert "IMPORT_OK" in result.stdout


def test_dev_with_default_secret_key_ok():
    result = _run({"DEBUG": "True", "SECRET_KEY": "dev-insecure-key-change-me"})
    assert result.returncode == 0, result.stderr
    assert "IMPORT_OK" in result.stdout
