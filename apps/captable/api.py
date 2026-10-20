"""
DRF endpoints for the cap table. Every UI action has one; all are org-scoped by path so
OrgContextMiddleware resolves ``request.org`` and answers non-members 403.

    GET    /api/v1/captable/orgs/<slug>/                     the computed table (members)
    PATCH  /api/v1/captable/orgs/<slug>/settings/            pool %, currency, issuer (admin)
    GET    /api/v1/captable/orgs/<slug>/holders/             who can hold a line (members)
    POST   /api/v1/captable/orgs/<slug>/rounds/              add a round (admin)
    PATCH  /api/v1/captable/orgs/<slug>/rounds/<id>/         edit a round (admin)
    DELETE /api/v1/captable/orgs/<slug>/rounds/<id>/         remove an empty round (admin)
    POST   /api/v1/captable/orgs/<slug>/investments/         add an investment (admin)
    PATCH  /api/v1/captable/orgs/<slug>/investments/<id>/    edit (admin)
    DELETE /api/v1/captable/orgs/<slug>/investments/<id>/    remove (admin)
    POST   /api/v1/captable/orgs/<slug>/holdings/            add stock of record (admin)
    PATCH  /api/v1/captable/orgs/<slug>/holdings/<id>/       edit (admin)
    DELETE /api/v1/captable/orgs/<slug>/holdings/<id>/       remove (admin)
    GET    /api/v1/captable/orgs/<slug>/ocf/                 OCF package as JSON (members)
    GET    /api/v1/captable/orgs/<slug>/ocf/?as=zip          the same files as <slug>.ocf.zip

A holder is named by ``holder`` = "m:<membership id>" or "h:<external holder id>", or a
new outside holder by ``holder_name`` (+ ``holder_type`` INDIVIDUAL / INSTITUTION).

Decimals go over the wire as strings. Writes answer with the recomputed table.
"""

import io
import zipfile
from decimal import InvalidOperation

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import path
from rest_framework.permissions import SAFE_METHODS
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.orgs.embed_auth import EmbedSessionAuthentication
from apps.orgs.models import MembershipRole

from . import ocf, services
from .models import Investment, Round, StockHolding


def is_cap_admin(request) -> bool:
    if request.user.is_authenticated and request.user.is_superuser:
        return True
    m = getattr(request, "membership", None)
    return m is not None and m.role == MembershipRole.ADMIN


def _s(v):
    return None if v is None else str(v)


def _line(ln):
    return {
        "kind": ln.kind,
        "label": ln.label,
        "pct": _s(ln.pct_display),
        "value": _s(ln.value),
        "membership_id": ln.membership_id,
        "holder_id": ln.holder_id,
        "holder_slug": ln.holder_slug,
        "pie_share_pct": _s(ln.pie_share_pct),
        "pie_total": _s(ln.pie_total),
        "investment_id": ln.investment_id,
        "round_id": ln.round_id,
        "round_name": ln.round_name,
        "instrument": ln.instrument,
        "amount": _s(ln.amount),
        "date": ln.date.isoformat() if ln.date else None,
        "note": ln.note,
    }


def table_dict(table):
    return {
        "org_slug": table.org_slug,
        "currency": table.currency,
        "unit_name": table.unit_name,
        "founders_pool_pct": _s(table.founders_pool_pct),
        "investor_pool_pct": _s(table.investor_pool_pct),
        "sold_pct": _s(table.sold_pct.quantize(services.PCT_Q)),
        "unsold_pct": _s(table.unsold_pct.quantize(services.PCT_Q)),
        "latest_post_money": _s(table.latest_post_money),
        "latest_round_name": table.latest_round_name,
        "total_pct": _s(table.total_pct),
        "total_value": _s(table.total_value),
        "lines": [_line(ln) for ln in table.lines],
        "rounds": [
            {
                "round_id": r.round_id,
                "name": r.name,
                "date": r.date.isoformat(),
                "instrument": r.instrument,
                "raised": _s(r.raised),
                "pre_money": _s(r.pre_money),
                "post_money": _s(r.post_money),
                "price_per_share": _s(r.price_per_share),
                "sold_pct": _s(r.sold_pct.quantize(services.PCT_Q)),
                "investment_count": r.investment_count,
            }
            for r in table.rounds
        ],
        "holdings": [
            {
                "holding_id": h.holding_id,
                "label": h.label,
                "membership_id": h.membership_id,
                "holder_id": h.holder_id,
                "stock_class": h.stock_class,
                "quantity": h.quantity,
                "pct_of_shares": _s(h.pct_of_shares),
                "date": h.date.isoformat() if h.date else None,
                "note": h.note,
            }
            for h in table.holdings
        ],
        "shares_of_record": table.shares_of_record,
    }


class _ReadOrEmbedWrite(EmbedSessionAuthentication):
    """Reads need only the session; writes need the embed header (the CSRF stand-in)."""

    def enforce_csrf(self, request):
        if request.method in SAFE_METHODS:
            return
        super().enforce_csrf(request)


class _CapView(APIView):
    authentication_classes = [_ReadOrEmbedWrite]

    def deny(self):
        return Response({"detail": "Only an admin can change the cap table."}, status=403)

    def table(self, request, status=200):
        return Response(table_dict(services.compute_cap_table(request.org)), status=status)

    def run(self, request, fn, status=200):
        if not is_cap_admin(request):
            return self.deny()
        try:
            fn()
        except (services.CapTableError, InvalidOperation, TypeError) as exc:
            msg = str(exc) if isinstance(exc, services.CapTableError) else "That is not a number."
            return Response({"detail": msg}, status=400)
        return self.table(request, status=status)

    def holder(self, request, data):
        if "holder" in data or "holder_name" in data:
            m, h = services.resolve_holder(
                request.org,
                key=data.get("holder", ""),
                new_name=data.get("holder_name", ""),
                new_type=data.get("holder_type", ""),
            )
            return {"membership": m, "holder": h}
        return {}


class SummaryView(_CapView):
    def get(self, request, org_slug):
        return self.table(request)


class SettingsView(_CapView):
    FIELDS = (
        "founders_pool_pct",
        "currency",
        "legal_name",
        "formation_date",
        "country_of_formation",
        "country_subdivision_of_formation",
    )

    def patch(self, request, org_slug):
        data = {k: request.data[k] for k in self.FIELDS if k in request.data}
        return self.run(request, lambda: services.set_settings(request.org, **data))


class HoldersView(_CapView):
    def get(self, request, org_slug):
        return Response({"holders": services.holder_choices(request.org)})


ROUND_FIELDS = services.ROUND_FIELDS


class RoundsView(_CapView):
    def post(self, request, org_slug):
        data = {k: request.data[k] for k in ROUND_FIELDS if k in request.data}
        return self.run(
            request, lambda: services.add_round(request.org, by=request.user, **data), status=201
        )


class RoundView(_CapView):
    def patch(self, request, org_slug, pk):
        rnd = get_object_or_404(Round.objects.for_org(request.org), pk=pk)
        data = {k: request.data[k] for k in ROUND_FIELDS if k in request.data}
        return self.run(request, lambda: services.update_round(rnd, **data))

    def delete(self, request, org_slug, pk):
        rnd = get_object_or_404(Round.objects.for_org(request.org), pk=pk)
        return self.run(request, lambda: services.delete_round(rnd))


class InvestmentsView(_CapView):
    def post(self, request, org_slug):
        def do():
            d = request.data
            rnd = get_object_or_404(Round.objects.for_org(request.org), pk=d.get("round"))
            services.add_investment(
                request.org,
                rnd,
                amount=d.get("amount"),
                date=d.get("date"),
                note=d.get("note", ""),
                by=request.user,
                **self.holder(request, d),
            )

        return self.run(request, do, status=201)


class InvestmentView(_CapView):
    def patch(self, request, org_slug, pk):
        inv = get_object_or_404(Investment.objects.for_org(request.org), pk=pk)

        def do():
            d = request.data
            data = {k: d[k] for k in ("amount", "date", "note") if k in d}
            if "round" in d:
                data["round"] = get_object_or_404(Round.objects.for_org(request.org), pk=d["round"])
            data.update(self.holder(request, d))
            services.update_investment(inv, **data)

        return self.run(request, do)

    def delete(self, request, org_slug, pk):
        inv = get_object_or_404(Investment.objects.for_org(request.org), pk=pk)
        return self.run(request, inv.delete)


class HoldingsView(_CapView):
    def post(self, request, org_slug):
        def do():
            d = request.data
            services.add_holding(
                request.org,
                quantity=d.get("quantity"),
                stock_class=d.get("stock_class", "Common"),
                date=d.get("date"),
                note=d.get("note", ""),
                by=request.user,
                **self.holder(request, d),
            )

        return self.run(request, do, status=201)


class HoldingView(_CapView):
    def patch(self, request, org_slug, pk):
        h = get_object_or_404(StockHolding.objects.for_org(request.org), pk=pk)

        def do():
            d = request.data
            data = {k: d[k] for k in ("quantity", "stock_class", "date", "note") if k in d}
            data.update(self.holder(request, d))
            services.update_holding(h, **data)

        return self.run(request, do)

    def delete(self, request, org_slug, pk):
        h = get_object_or_404(StockHolding.objects.for_org(request.org), pk=pk)
        return self.run(request, h.delete)


def ocf_zip(org) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in ocf.package_bytes(ocf.build_package(org)).items():
            zf.writestr(name, body)
    return buf.getvalue()


class OcfView(_CapView):
    def get(self, request, org_slug):
        # ``format`` is DRF's own suffix parameter, so the zip is asked for as ?as=zip.
        if request.query_params.get("as") == "zip":
            resp = HttpResponse(ocf_zip(request.org), content_type="application/zip")
            resp["Content-Disposition"] = f'attachment; filename="{request.org.slug}.ocf.zip"'
            return resp
        return Response(ocf.build_package(request.org))


ORG = "orgs/<slug:org_slug>/"

urlpatterns = [
    path(ORG, SummaryView.as_view(), name="captable-summary"),
    path(ORG + "settings/", SettingsView.as_view(), name="captable-settings"),
    path(ORG + "holders/", HoldersView.as_view(), name="captable-holders"),
    path(ORG + "rounds/", RoundsView.as_view(), name="captable-rounds"),
    path(ORG + "rounds/<int:pk>/", RoundView.as_view(), name="captable-round"),
    path(ORG + "investments/", InvestmentsView.as_view(), name="captable-investments"),
    path(ORG + "investments/<int:pk>/", InvestmentView.as_view(), name="captable-investment"),
    path(ORG + "holdings/", HoldingsView.as_view(), name="captable-holdings"),
    path(ORG + "holdings/<int:pk>/", HoldingView.as_view(), name="captable-holding"),
    path(ORG + "ocf/", OcfView.as_view(), name="captable-ocf"),
]
