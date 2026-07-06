"""
DRF endpoints for import/export (API-first: every UI action has an endpoint).

Routes are mounted under an `o/<org_slug>/` prefix so OrgContextMiddleware resolves
`request.org` / `request.membership` and enforces org membership on the API exactly as it
does for the HTML pages:

    GET  /api/v1/exports/o/<org_slug>/batches/            list import batches (audit)
    GET  /api/v1/exports/o/<org_slug>/batches/<pk>/       one batch
    POST /api/v1/exports/o/<org_slug>/batches/import_csv/ upload a CSV (admin/steward)
    GET  /api/v1/exports/o/<org_slug>/export/<format>/    download an export as CSV

Every queryset is scoped to `request.org`.
"""

from django.http import HttpResponse
from django.urls import path
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.orgs.models import MembershipRole

from .exporters import EXPORTERS, get_exporter
from .models import ImportBatch
from .services import CsvImportError, MODE_REPLACE, VALID_MODES, import_opening_balances

IMPORT_ROLES = (MembershipRole.ADMIN, MembershipRole.STEWARD)


def _may_import(request) -> bool:
    if request.user.is_authenticated and request.user.is_superuser:
        return True
    membership = getattr(request, "membership", None)
    return membership is not None and membership.role in IMPORT_ROLES


class ImportBatchSerializer(serializers.ModelSerializer):
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = ImportBatch
        fields = [
            "id",
            "kind",
            "kind_display",
            "filename",
            "row_count",
            "notes",
            "created_at",
        ]


class ImportBatchViewSet(viewsets.ReadOnlyModelViewSet):
    """Audit list/detail of import batches, plus the CSV import action."""

    serializer_class = ImportBatchSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        return ImportBatch.objects.for_org(self.request.org)

    def import_csv(self, request, org_slug=None):
        if not _may_import(request):
            return Response(
                {"detail": "Importing opening balances requires an admin or steward role."},
                status=status.HTTP_403_FORBIDDEN,
            )
        upload = request.FILES.get("file")
        if upload is None:
            return Response(
                {"detail": "Provide a CSV file under the 'file' field."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        mode = request.data.get("mode", MODE_REPLACE)
        if mode not in VALID_MODES:
            mode = MODE_REPLACE
        try:
            result = import_opening_balances(
                request.org, upload, created_by=request.user, mode=mode
            )
        except CsvImportError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "batch": ImportBatchSerializer(result.batch).data,
                "created": result.created,
                "skipped": result.skipped,
                "errors": [
                    {"line": e.line_number, "identifier": e.identifier, "message": e.message}
                    for e in result.errors
                ],
            },
            status=status.HTTP_201_CREATED,
        )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def export_view(request, org_slug=None, format_key=None):
    """Return a registered export target as a CSV download."""
    try:
        exporter = get_exporter(format_key)
    except KeyError:
        return Response(
            {"detail": f"Unknown export format '{format_key}'. Available: {list(EXPORTERS)}"},
            status=status.HTTP_404_NOT_FOUND,
        )
    response = HttpResponse(exporter.to_csv(request.org), content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="{exporter.download_filename(request.org)}"'
    )
    return response


urlpatterns = [
    path(
        "o/<slug:org_slug>/batches/import_csv/",
        ImportBatchViewSet.as_view({"post": "import_csv"}),
        name="importbatch-import-csv",
    ),
    path(
        "o/<slug:org_slug>/batches/",
        ImportBatchViewSet.as_view({"get": "list"}),
        name="importbatch-list",
    ),
    path(
        "o/<slug:org_slug>/batches/<int:pk>/",
        ImportBatchViewSet.as_view({"get": "retrieve"}),
        name="importbatch-detail",
    ),
    path(
        "o/<slug:org_slug>/export/<slug:format_key>/",
        export_view,
        name="exports-export",
    ),
]
