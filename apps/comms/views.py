"""
Comms — the compose screen IS the email.

A thin border holds exactly what gets sent. Everything inside it is editable
where it sits and nothing inside it is a control: deleting a line is how you cut
it, and the cut line comes back as a chip underneath, outside the border. Every
button lives outside (abra `workersvc-comms-wysiwyg`).

Admin-only. Every route except the sent page goes through `_admin_only`, which
asks `sources/govkit.py` — nothing here imports a GovKit model.
"""

import json
from functools import wraps
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from . import rewrite as editor
from . import services
from .models import AUDIENCE_KEYS, SUPPORTERS, Edition, Send
from .sources import crm, govkit


def _admin_only(view):
    @wraps(view)
    @login_required
    def wrapped(request, org_slug, *args, **kwargs):
        if not govkit.viewer_is_admin(request):
            raise PermissionDenied("Comms is for org admins.")
        return view(request, org_slug, *args, **kwargs)

    return wrapped


def _audience(request) -> str:
    audience = request.GET.get("t") or request.POST.get("t") or AUDIENCE_KEYS[0]
    if audience not in AUDIENCE_KEYS:
        raise Http404("No such audience.")
    return audience


def _back(org_slug, audience) -> str:
    return f"{reverse('comms:index', kwargs={'org_slug': org_slug})}?t={audience}"


@_admin_only
def index(request, org_slug):
    """The week ahead, as the email one audience will get."""
    audience = _audience(request)
    edition = services.open_edition(org_slug)
    send = services.send_for(edition, audience)
    _missing, problem = services.missing_events(edition)
    crm_tags, crm_problem = crm.tags(org_slug) if audience == SUPPORTERS else ([], "")

    page_url = ""
    if send.public_token and send.is_published:
        page_url = request.build_absolute_uri(
            reverse("comms_public:bulletin", kwargs={"token": send.public_token})
        )

    return render(
        request,
        "comms/index.html",
        {
            "page_title": "Comms",
            "org_slug": org_slug,
            "edition": edition,
            "send": send,
            "audience": audience,
            "audiences": services.audience_state(edition, org_slug),
            "sections": services.email(edition, audience),
            "cut": services.cut_items(edition, audience),
            "problem": problem,
            "calendar_url": govkit.calendar_url(org_slug),
            "calendar_settings_url": govkit.calendar_settings_url(org_slug),
            "default_send_at": services.default_send_at(edition),
            "page_url": page_url,
            "plain_text": services.plain_text(edition, audience, send.subject),
            "html_text": services.html_body(edition, audience, send.subject),
            "recipient_emails": ", ".join(services.recipient_emails(org_slug, audience)),
            # Gmail's compose URL takes a subject; a formatted body cannot ride
            # a URL, so the email itself goes on the clipboard and gets pasted.
            "gmail_url": (
                "https://mail.google.com/mail/?view=cm&fs=1&su=" + quote(send.subject or "")
            ),
            "can_ask": editor.available(),
            "past": _past(org_slug),
            # Supporters is a list we build up rather than a role, so it is the
            # one audience with a way to bring people in.
            "is_list": audience == SUPPORTERS,
            "list_size": services.audience_size(org_slug, audience),
            "crm_tags": crm_tags,
            "crm_problem": crm_problem,
            "crm_ready": crm.available(),
        },
    )


def _past(org_slug: str) -> list[dict]:
    """What already went out, one line each, opening where it sits."""
    from .models import AUDIENCES

    labels = dict(AUDIENCES)
    sends = (
        Send.objects.filter(edition__org_slug=org_slug, sent_at__isnull=False)
        .select_related("edition")
        .order_by("-sent_at")[:20]
    )
    return [
        {
            "send": s,
            "label": labels.get(s.audience, s.audience),
            "sections": services.email(s.edition, s.audience),
        }
        for s in sends
    ]


@_admin_only
@require_POST
def save(request, org_slug, pk):
    """Read one part of the email back after a person typed in it.

    `what` is `subject`, `section-title`, or a section key. A section arrives as
    the lines the browser read out of it; anything no longer there was deleted,
    which is how this design cuts a line.
    """
    edition = get_object_or_404(Edition, pk=pk, org_slug=org_slug)
    audience = _audience(request)
    what = request.POST.get("what", "")
    value = request.POST.get("value", "")

    if what == "subject":
        send = services.send_for(edition, audience)
        send.subject = value.strip()[:200]
        send.save(update_fields=["subject", "updated_at"])
    elif what == "section-title":
        services.set_section_title(edition, request.POST.get("sec", ""), value)
    elif edition.section(what) is not None:
        try:
            rows = json.loads(request.POST.get("rows", "[]"))
        except ValueError:
            return JsonResponse({"saved": False}, status=400)
        changed = services.save_section(edition, audience, what, rows)
        return JsonResponse({"saved": True, "redraw": changed})
    else:
        return JsonResponse({"saved": False}, status=400)

    return JsonResponse({"saved": True, "redraw": False})


@_admin_only
@require_POST
def put_back(request, org_slug, pk, item_id):
    """A chip was pressed: the line goes back into this audience's email."""
    edition = get_object_or_404(Edition, pk=pk, org_slug=org_slug)
    audience = _audience(request)
    services.restore_item(edition, audience, item_id)
    return redirect(_back(org_slug, audience))


@_admin_only
@require_POST
def add_from_calendar(request, org_slug, pk):
    edition = get_object_or_404(Edition, pk=pk, org_slug=org_slug)
    services.add_event(edition, request.POST.get("uid", ""))
    return redirect(_back(org_slug, _audience(request)))


@_admin_only
@require_POST
def refresh_calendar(request, org_slug, pk):
    """Read the calendar again now, for a meeting added or renamed a minute ago.

    The lines already in the email take the calendar's word for when a meeting
    is and what it is called; anything a person wrote about it stands, unless
    they tick the box to say the calendar wins.
    """
    edition = get_object_or_404(Edition, pk=pk, org_slug=org_slug)
    services.reread_calendar(edition, overwrite=request.POST.get("overwrite") == "1")
    return redirect(_back(org_slug, _audience(request)))


@_admin_only
@require_POST
def import_list(request, org_slug, pk):
    """Bring a CRM tag onto the supporters list.

    The list is built up an import at a time, so this is safe to press again:
    it adds whoever is new and refreshes the rest from the CRM, and it never
    removes anybody — leaving a tag is not the same as asking us to stop.
    """
    get_object_or_404(Edition, pk=pk, org_slug=org_slug)
    audience = _audience(request)
    try:
        tag_id = int(request.POST.get("tag", ""))
    except ValueError:
        messages.error(request, "Pick a tag first.")
        return redirect(_back(org_slug, audience))

    tag_name = request.POST.get("tag_name", "")
    added, refreshed, problem = services.import_from_crm(org_slug, audience, tag_id, tag_name)
    if problem:
        messages.error(request, problem)
    else:
        messages.info(
            request,
            f"{added} new from {tag_name}, {refreshed} already here. "
            f"{services.audience_size(org_slug, audience)} on the list.",
        )
    return redirect(_back(org_slug, audience))


@_admin_only
@require_POST
def ask(request, org_slug, pk):
    """The line at the bottom: say what to change, in words."""
    edition = get_object_or_404(Edition, pk=pk, org_slug=org_slug)
    audience = _audience(request)
    send = services.send_for(edition, audience)
    _changed, problem = editor.apply(edition, audience, send, request.POST.get("instruction", ""))
    if problem:
        messages.error(request, problem)
    return redirect(_back(org_slug, audience))


@_admin_only
@require_POST
def undo(request, org_slug, pk):
    edition = get_object_or_404(Edition, pk=pk, org_slug=org_slug)
    if edition.previous:
        edition.restore(edition.previous)
        edition.previous = None
        edition.save(update_fields=["sections", "items", "previous", "updated_at"])
    return redirect(_back(org_slug, _audience(request)))


@_admin_only
@require_POST
def schedule(request, org_slug, pk):
    """Write a date on it. Scheduling is the default; nothing sends itself."""
    edition = get_object_or_404(Edition, pk=pk, org_slug=org_slug)
    audience = _audience(request)
    send = services.send_for(edition, audience)
    when = parse_datetime(request.POST.get("when", ""))
    if when is None:
        messages.error(request, "That date did not read as a date.")
    else:
        if timezone.is_naive(when):
            when = when.replace(tzinfo=services.zone(edition))
        services.schedule(send, when)
    return redirect(_back(org_slug, audience))


@_admin_only
@require_POST
def send_now(request, org_slug, pk):
    """A human presses this. It also puts the week on its page.

    Delivery is not wired yet: this records that it went and publishes the page,
    which is what the link in the email points at. Wiring SMTP is a separate
    step and does not change anything above it.
    """
    edition = get_object_or_404(Edition, pk=pk, org_slug=org_slug)
    audience = _audience(request)
    send = services.send_for(edition, audience)
    services.mark_sent(send, govkit.audience_size(org_slug, audience) or 0)
    messages.info(
        request,
        "Marked as sent and the page is up. Delivery is not wired yet — "
        "copy the text and send it from your mail client.",
    )
    return redirect(_back(org_slug, audience))


@_admin_only
@require_POST
def publish(request, org_slug, pk):
    """Put this week on its page, without saying it has been sent.

    An email copied out and pasted into Gmail still needs a page to link to,
    and a link to paste into chat.
    """
    edition = get_object_or_404(Edition, pk=pk, org_slug=org_slug)
    audience = _audience(request)
    services.publish(services.send_for(edition, audience))
    return redirect(_back(org_slug, audience))


@_admin_only
@require_POST
def unpublish(request, org_slug, pk):
    """Take the page down. Putting it back is the same address."""
    edition = get_object_or_404(Edition, pk=pk, org_slug=org_slug)
    audience = _audience(request)
    services.unpublish(services.send_for(edition, audience))
    return redirect(_back(org_slug, audience))


@_admin_only
@require_POST
def reopen(request, org_slug, pk):
    """Change your mind: back to being written, page and date cleared."""
    edition = get_object_or_404(Edition, pk=pk, org_slug=org_slug)
    audience = _audience(request)
    services.unschedule(services.send_for(edition, audience))
    return redirect(_back(org_slug, audience))


def public_bulletin(request, token):
    """The week's page. Not published means not there."""
    send = get_object_or_404(
        Send.objects.select_related("edition"),
        public_token=token,
        published_at__isnull=False,
    )
    edition = send.edition
    return render(
        request,
        "comms/public.html",
        {
            "send": send,
            "edition": edition,
            "sections": services.email(edition, send.audience),
            "org_name": govkit.display_name(edition.org_slug),
        },
    )


public_bulletin.org_context_exempt = True
