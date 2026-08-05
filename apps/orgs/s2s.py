"""The one check every server-to-server endpoint on this install runs.

The caller is another SERVER holding a shared bearer secret (the workers.vc
doorway, amebo), not a browser session, so none of Django's session or DRF's
permission machinery applies. An empty GOVKIT_S2S_TOKEN disables every S2S
endpoint rather than accepting anything: a deployment that forgot to set the
secret must be shut, not open.

Kept in one module so a second caller cannot arrive with a second, subtly
different, comparison.
"""

from __future__ import annotations

import secrets

from django.conf import settings


def authorized(request) -> bool:
    expected = settings.GOVKIT_S2S_TOKEN
    if not expected:
        return False
    supplied = request.headers.get("Authorization", "")
    return secrets.compare_digest(supplied, f"Bearer {expected}")
