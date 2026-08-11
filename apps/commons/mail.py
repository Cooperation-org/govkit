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


def welcome_new_member(membership, note="") -> None:
    """Tell the person they are in, and where their team's dash is.

    Someone who raised a hand and was let in has no other way of finding out:
    the row they created lives on the team's rail, not theirs. Without this the
    join happens in a place they cannot see. No-op without SMTP.
    """
    if not mail_configured():
        return
    to = membership.user.email
    if not to:
        return
    from apps.orgs.invites import cohort_front_door_url

    org = membership.org
    lines = [f"You are in — you are now a member of {org.display_name}."]
    where = cohort_front_door_url(org)
    if where:
        lines.append(f"Your team's dash: {where}")
    if note:
        lines.append(note)
    try:
        send_mail(
            subject=f"You are in — {org.display_name}",
            message="\n\n".join(lines),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[to],
            fail_silently=True,
        )
    except Exception:
        logger.exception("welcome mail failed (org=%s)", org.slug)


def notify_sponsor_pledge(pledge) -> None:
    """Tell the org's admins someone offered to sponsor. No-op without SMTP.

    Money offered and not answered is the one thing on the rail that costs
    real money to miss, so it is mailed as well as railed.
    """
    if not mail_configured():
        return
    admins = Membership.objects.filter(org=pledge.org, role=MembershipRole.ADMIN).select_related(
        "user"
    )
    recipients = [m.user.email for m in admins if m.user.email]
    if not recipients:
        return
    lines = [f"{pledge.who} offered to sponsor {pledge.org.display_name}: {pledge.summary}."]
    if pledge.tier:
        lines.append(f"Tier: {pledge.tier}")
    if pledge.offer:
        lines.append(f"Offering: {pledge.offer}")
    if pledge.note:
        lines.append(f"In their words:\n{pledge.note}")
    lines.append(
        "Listing: " + (f"as “{pledge.public_name}”" if pledge.public_name else "asked not to be listed")
    )
    lines.append(
        f"Write back to {pledge.email}, then mark it answered on the dashboard so it"
        " leaves the rail."
    )
    try:
        send_mail(
            subject=f"{pledge.who} offered to sponsor {pledge.org.display_name} ({pledge.summary})",
            message="\n\n".join(lines),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            fail_silently=True,
        )
    except Exception:
        logger.exception("sponsor-pledge mail failed (org=%s)", pledge.org.slug)


def confirm_sponsor_pledge(pledge) -> None:
    """Tell the sponsor we have them. No-op without SMTP.

    Without this the person's whole journey ends on a web page they will close.
    A pledge is a promise made to a stranger; the least we owe them is a record
    in their own inbox of what they said and that a human is coming.
    """
    if not mail_configured():
        return
    body = [
        f"Thank you — we have your offer to sponsor {pledge.org.display_name}: {pledge.summary}.",
    ]
    if pledge.offer:
        body.append(f"You offered: {pledge.offer}")
    body.append(
        "Nothing is charged and nothing is owed. Someone from the team will write"
        " back to you to settle how to send it."
    )
    body.append("If any of this is wrong, just reply to this message.")
    try:
        send_mail(
            subject=f"Your sponsorship of {pledge.org.display_name}",
            message="\n\n".join(body),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[pledge.email],
            fail_silently=True,
        )
    except Exception:
        logger.exception("sponsor-pledge confirmation failed (pledge=%s)", pledge.pk)
