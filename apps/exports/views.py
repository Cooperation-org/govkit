"""
exports views: an import page (upload CSV, show batch result/errors) and CSV download
links for each registered export target.

Import is admin/steward-gated (it writes equity); exports are readable by any member of
the org. OrgContextMiddleware has already resolved `request.org` / `request.membership`
and rejected non-members, so the views only need the extra role check for import.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.orgs.models import MembershipRole

from .exporters import EXPORTERS, get_exporter
from .models import ImportBatch
from .services import CsvImportError, MODE_REPLACE, VALID_MODES, import_opening_balances

IMPORT_ROLES = (MembershipRole.ADMIN, MembershipRole.STEWARD)


def _may_import(request) -> bool:
    """Import writes equity — admins/stewards (and superusers) only."""
    if request.user.is_authenticated and request.user.is_superuser:
        return True
    membership = request.membership
    return membership is not None and membership.role in IMPORT_ROLES


def _export_links():
    return [{"format_key": key, "label": exporter.label} for key, exporter in EXPORTERS.items()]


def _index_context(request, org_slug, *, mode=MODE_REPLACE, last_result=None):
    return {
        "page_title": "Import / Export",
        "org_slug": org_slug,
        "can_import": _may_import(request),
        "exports": _export_links(),
        "recent_batches": ImportBatch.objects.for_org(request.org)[:20],
        "modes": VALID_MODES,
        "default_mode": mode,
        "last_result": last_result,
    }


@login_required
def index(request, org_slug):
    """Import form + export download links + recent import-batch audit list."""
    return render(request, "exports/index.html", _index_context(request, org_slug))


@login_required
def import_upload(request, org_slug):
    """Handle the opening-balance CSV upload (admin/steward)."""
    if not _may_import(request):
        raise PermissionDenied("Importing opening balances requires an admin or steward role.")
    if request.method != "POST":
        return redirect(reverse("exports:index", kwargs={"org_slug": org_slug}))

    upload = request.FILES.get("file")
    mode = request.POST.get("mode", MODE_REPLACE)
    if mode not in VALID_MODES:
        mode = MODE_REPLACE
    if upload is None:
        messages.error(request, "Choose a CSV file to import.")
        return redirect(reverse("exports:index", kwargs={"org_slug": org_slug}))

    try:
        result = import_opening_balances(request.org, upload, created_by=request.user, mode=mode)
    except CsvImportError as exc:
        messages.error(request, f"Import failed: {exc}")
        return redirect(reverse("exports:index", kwargs={"org_slug": org_slug}))

    if result.errors:
        messages.warning(
            request,
            f"Imported {result.created} row(s); {len(result.errors)} row(s) rejected"
            + (f", {result.skipped} skipped." if result.skipped else "."),
        )
    else:
        messages.success(
            request,
            f"Imported {result.created} opening balance(s)"
            + (f" ({result.skipped} skipped)." if result.skipped else "."),
        )

    return render(
        request,
        "exports/index.html",
        _index_context(request, org_slug, mode=mode, last_result=result),
    )


@login_required
def export_csv(request, org_slug, format_key):
    """Stream a registered export target as a CSV download. Readable by any member."""
    try:
        exporter = get_exporter(format_key)
    except KeyError:
        raise PermissionDenied("Unknown export format.")
    body = exporter.to_csv(request.org)
    response = HttpResponse(body, content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="{exporter.download_filename(request.org)}"'
    )
    return response
