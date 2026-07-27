"""Session auth for cross-origin embed writes.

Lives on its own (not in ``orgs.api``) because every app whose embed component
writes needs it — the checklist toggle and the task sheet both, and whatever
comes next. Importing a whole api module to get one auth class is how circular
imports start.
"""

from rest_framework.authentication import SessionAuthentication


class EmbedSessionAuthentication(SessionAuthentication):
    """Session auth for cross-origin embed writes.

    Django's CSRF token never leaves the GovKit origin, so the dash cannot echo
    it. The replacement is the standard preflight gate: the request must carry a
    custom header (X-Govkit-Embed), which browsers only send cross-origin after
    a CORS preflight our allowlist answers — a forged form on a random site can
    neither set the header nor pass preflight. Cookies stay SameSite=Lax.
    """

    def enforce_csrf(self, request):
        if not request.headers.get("X-Govkit-Embed"):
            from rest_framework.exceptions import PermissionDenied as DrfPermissionDenied

            raise DrfPermissionDenied("Missing embed header.")
