"""
Outbound mail for the commons — one function, one message.

GovKit installs may have no SMTP at all (the cohort VM doesn't yet). The rule:
if settings.EMAIL_HOST or DEFAULT_FROM_EMAIL is empty, sending is OFF and this
module does nothing — the interest feeds carry the signal instead. When SMTP
env vars are set (earnkit roles/govkit), notifications start without a code
change. Sends never raise: a mail failure must not fail the interest write.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail

from apps.orgs.models import Membership, MembershipRole

logger = logging.getLogger(__name__)


def mail_configured() -> bool:
    return bool(settings.EMAIL_HOST and settings.DEFAULT_FROM_EMAIL)


def notify_venture_interest(interest) -> None:
    """Tell the venture's admins someone wants to join. No-op without SMTP."""
    if not mail_configured():
        return
    admins = Membership.objects.filter(org=interest.org, role=MembershipRole.ADMIN).select_related(
        "user"
    )
    recipients = [m.user.email for m in admins if m.user.email]
    if not recipients:
        return
    who = interest.user.get_full_name() or interest.user.email
    body = f"{who} wants to join {interest.org.display_name}."
    if interest.note:
        body += f"\n\nIn their words:\n{interest.note}"
    body += (
        f"\n\nReply to them at {interest.user.email}, then mark it answered on your"
        " dashboard so it leaves the waiting list."
    )
    try:
        send_mail(
            subject=f"{who} wants to join {interest.org.display_name}",
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            fail_silently=True,
        )
    except Exception:
        logger.exception("venture-interest mail failed (org=%s)", interest.org.slug)
