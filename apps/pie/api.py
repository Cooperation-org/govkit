"""
DRF endpoints for the pie (API-first — every UI action has an endpoint).

Two read-only endpoints, both mounted under ``orgs/<org_slug>/`` so OrgContextMiddleware
resolves ``request.org`` / ``request.membership`` and enforces membership (403 for
non-members) exactly as it does for the HTML pages:

    GET /api/v1/pie/orgs/<org_slug>/summary/    → the whole org pie + per-member provenance
    GET /api/v1/pie/orgs/<org_slug>/standing/   → the requesting member's personal standing

Decimals are serialised as strings to preserve precision on the wire.
"""

from django.urls import path
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import compute_personal_standing, compute_pie


def _dec(value):
    return str(value)


def _task_dict(task):
    return {
        "task_id": task.task_id,
        "external_id": task.external_id,
        "external_url": task.external_url,
        "subject": task.subject,
    }


def _line_dict(line):
    return {
        "line_id": line.line_id,
        "run_id": line.run_id,
        "computed_value": _dec(line.computed_value),
        "adjustment": _dec(line.adjustment),
        "adjustment_reason": line.adjustment_reason,
        "final_value": _dec(line.final_value),
        "tasks": [_task_dict(t) for t in line.tasks],
    }


def _opening_dict(ob):
    return {
        "opening_balance_id": ob.opening_balance_id,
        "value": _dec(ob.value),
        "source_note": ob.source_note,
    }


def _slice_dict(s):
    return {
        "membership_id": s.membership_id,
        "member_label": s.member_label,
        "role": s.role,
        "drops_total": _dec(s.drops_total),
        "opening_total": _dec(s.opening_total),
        "issued_total": _dec(s.issued_total),
        "share": _dec(s.share),
        "share_pct": _dec(s.share_pct),
        "lines": [_line_dict(ln) for ln in s.lines],
        "opening_balances": [_opening_dict(ob) for ob in s.opening_balances],
    }


class PieSummaryView(APIView):
    """The org pie: total issued equity and every member's traceable slice."""

    def get(self, request, org_slug):
        pie = compute_pie(request.org)
        return Response(
            {
                "org_slug": pie.org_slug,
                "unit_name": pie.unit_name,
                "total": _dec(pie.total),
                "member_count": pie.member_count,
                "slices": [_slice_dict(s) for s in pie.slices],
            }
        )


class PersonalStandingView(APIView):
    """The requesting member's personal standing: issued vs pending, all traceable."""

    def get(self, request, org_slug):
        membership = request.membership
        if membership is None:
            return Response(
                {"detail": "No membership in this org to report standing for."},
                status=404,
            )
        st = compute_personal_standing(request.org, membership)
        return Response(
            {
                "org_slug": st.org_slug,
                "unit_name": st.unit_name,
                "membership_id": st.membership_id,
                "member_label": st.member_label,
                "issued_total": _dec(st.issued_total),
                "opening_total": _dec(st.opening_total),
                "realized_total": _dec(st.realized_total),
                "share": _dec(st.share),
                "share_pct": _dec(st.share_pct),
                "issued_lines": [_line_dict(ln) for ln in st.issued_lines],
                "opening_balances": [_opening_dict(ob) for ob in st.opening_balances],
                "pending_total": _dec(st.pending_total),
                "pending_lines": [_line_dict(ln) for ln in st.pending_lines],
            }
        )


urlpatterns = [
    path("orgs/<slug:org_slug>/summary/", PieSummaryView.as_view(), name="pie-summary"),
    path("orgs/<slug:org_slug>/standing/", PersonalStandingView.as_view(), name="pie-standing"),
]
