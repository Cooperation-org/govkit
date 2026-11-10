"""
The Access page: the <govkit-access> card, and for org admins the tool connections.

Every action here has an endpoint in api.py. Org context is set by OrgContextMiddleware
for these ``/o/<org_slug>/access/`` routes.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .api import _is_admin
from .forms import ToolConnectionForm
from .models import ToolConnection
from .services import can_grant


def embed_base(request):
    """GovKit's own origin plus base path, for a card's data-up on GovKit's pages."""
    return request.build_absolute_uri(reverse("orgs:landing")).rstrip("/")


def _context(request, form=None, editing=None):
    is_admin = _is_admin(request)
    if is_admin and form is None:
        form = ToolConnectionForm(instance=editing)
    return {
        "embed_up": embed_base(request),
        "is_admin": is_admin,
        "connections": ToolConnection.objects.for_org(request.org) if is_admin else [],
        "form": form,
        "editing": editing,
    }


@login_required
def index(request, org_slug):
    if not can_grant(request):
        raise PermissionDenied("Only org admins and people they allow may change tool access.")
    editing = None
    if _is_admin(request) and request.GET.get("tool", "").isdigit():
        editing = get_object_or_404(
            ToolConnection.objects.for_org(request.org), pk=request.GET["tool"]
        )
    return render(request, "access/index.html", _context(request, editing=editing))


@login_required
@require_POST
def save_tool(request, org_slug):
    if not _is_admin(request):
        raise PermissionDenied("Only org admins may connect tools.")
    tool_id = request.POST.get("tool_id", "")
    editing = (
        get_object_or_404(ToolConnection.objects.for_org(request.org), pk=tool_id)
        if tool_id.isdigit()
        else None
    )
    instance = editing or ToolConnection(org=request.org)
    form = ToolConnectionForm(request.POST, instance=instance)
    if form.is_valid():
        form.save()
        messages.success(request, f"Saved {form.instance.display_name}.")
        return redirect("access:index", org_slug=org_slug)
    return render(request, "access/index.html", _context(request, form=form, editing=editing))
