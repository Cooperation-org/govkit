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
    GET    /api/v1/captable/orgs/<slug>/stock-classes/       the classes (members)
    POST   /api/v1/captable/orgs/<slug>/stock-classes/       add a class (admin)
    PATCH  /api/v1/captable/orgs/<slug>/stock-classes/<id>/  edit (admin)
    DELETE /api/v1/captable/orgs/<slug>/stock-classes/<id>/  remove an unused class (admin)
    GET    /api/v1/captable/orgs/<slug>/valuations/          409A and round prices (members)
    POST   /api/v1/captable/orgs/<slug>/valuations/          add (admin)
    PATCH  /api/v1/captable/orgs/<slug>/valuations/<id>/     edit (admin)
    DELETE /api/v1/captable/orgs/<slug>/valuations/<id>/     remove (admin)
    GET    /api/v1/captable/orgs/<slug>/franchise-tax/?year= Delaware tax, both methods (members)
    PATCH  /api/v1/captable/orgs/<slug>/franchise-tax/       {year, gross_assets, issued_shares} (admin)
    GET    /api/v1/captable/orgs/<slug>/events/?limit=       every change, newest first (members)
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
from django.utils import timezone
from django.urls import path
from rest_framework.permissions import SAFE_METHODS
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.orgs.embed_auth import EmbedSessionAuthentication
from apps.orgs.models import MembershipRole

from . import ocf, services
from .models import EntityType, Investment, Round, Scope, StockClass, StockHolding, Valuation


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


def _round_terms(rnd):
    return {
        "conversion_valuation_cap": _s(rnd.conversion_valuation_cap),
        "conversion_discount": _s(rnd.conversion_discount),
        "conversion_timing": rnd.conversion_timing,
        "conversion_mfn": rnd.conversion_mfn,
        "pro_rata": rnd.pro_rata,
        "interest_rate": _s(rnd.interest_rate),
        "maturity_date": rnd.maturity_date.isoformat() if rnd.maturity_date else None,
    }


def stock_class_dict(c):
    return {
        "id": c.pk,
        "name": c.name,
        "class_type": c.class_type,
        "initial_shares_authorized": c.initial_shares_authorized,
        "par_value": _s(c.par_value),
        "votes_per_share": _s(c.votes_per_share),
        "seniority": c.seniority,
        "board_approval_date": c.board_approval_date.isoformat() if c.board_approval_date else None,
        "issued": getattr(c, "issued", None),
    }


def valuation_dict(v):
    return {
        "id": v.pk,
        "stock_class_id": v.stock_class_id,
        "stock_class": v.stock_class.name,
        "effective_date": v.effective_date.isoformat(),
        "price_per_share": _s(v.price_per_share),
        "valuation_type": v.valuation_type,
        "provider": v.provider,
        "board_approval_date": v.board_approval_date.isoformat() if v.board_approval_date else None,
        "report_url": v.report_url,
        "gross_assets": _s(v.gross_assets),
    }


def franchise_dict(ft):
    return {
        "year": ft.year,
        "due_date": ft.due_date.isoformat(),
        "gross_assets": _s(ft.gross_assets),
        "issued_shares": ft.issued_shares,
        "issued_shares_stated": ft.issued_shares_stated,
        "authorized_shares": ft.authorized_shares,
        "classes_missing_authorized": ft.classes_missing_authorized,
        "authorized_shares_method": _s(ft.authorized_shares_method),
        "assumed_par": _s(ft.assumed_par),
        "assumed_par_value_capital": _s(ft.assumed_par_value_capital),
        "assumed_par_value_capital_method": _s(ft.assumed_par_value_capital_method),
        "lesser": _s(ft.lesser),
        "lesser_method": ft.lesser_method,
    }


def event_dict(e):
    return {
        "at": e.at.isoformat(),
        "actor": e.actor,
        "kind": e.kind,
        "object_type": e.object_type,
        "object_id": e.object_id,
        "label": e.label,
        "text": e.text,
        "before": e.before,
        "after": e.after,
    }


def table_dict(table, org=None):
    cfg = services.get_settings(org) if org is not None else None
    terms = {r.pk: r for r in Round.objects.for_org(org)} if org is not None else {}
    out = {
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
                **(_round_terms(terms[r.round_id]) if r.round_id in terms else {}),
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
                "stock_class_id": h.stock_class_id,
                "quantity": h.quantity,
                "pct_of_shares": _s(h.pct_of_shares),
                "date": h.date.isoformat() if h.date else None,
                "note": h.note,
            }
            for h in table.holdings
        ],
        "shares_of_record": table.shares_of_record,
        "scope": table.scope,
    }
    if cfg is not None:
        out["entity_type"] = cfg.entity_type
        out["incorporated"] = cfg.incorporated
        out["stock_classes"] = [stock_class_dict(c) for c in services.stock_classes(org)]
        out["choices"] = {
            "entity_type": EntityType.choices,
            "scope": Scope.choices,
        }
    return out


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
        return Response(
            table_dict(services.compute_cap_table(request.org), request.org), status=status
        )

    def run(self, request, fn, status=200, answer=None):
        if not is_cap_admin(request):
            return self.deny()
        try:
            fn()
        except (services.CapTableError, InvalidOperation, TypeError, ValueError) as exc:
            msg = str(exc) if isinstance(exc, services.CapTableError) else "That is not a number."
            return Response({"detail": msg}, status=400)
        return answer() if answer else self.table(request, status=status)

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
        "entity_type",
        "incorporated",
        "scope",
    )

    def patch(self, request, org_slug):
        data = {k: request.data[k] for k in self.FIELDS if k in request.data}
        return self.run(
            request, lambda: services.set_settings(request.org, by=request.user, **data)
        )


class HoldersView(_CapView):
    def get(self, request, org_slug):
        return Response({"holders": services.holder_choices(request.org)})


ROUND_FIELDS = services.ROUND_INPUTS


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
        return self.run(request, lambda: services.update_round(rnd, by=request.user, **data))

    def delete(self, request, org_slug, pk):
        rnd = get_object_or_404(Round.objects.for_org(request.org), pk=pk)
        return self.run(request, lambda: services.delete_round(rnd, by=request.user))


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
            services.update_investment(inv, by=request.user, **data)

        return self.run(request, do)

    def delete(self, request, org_slug, pk):
        inv = get_object_or_404(Investment.objects.for_org(request.org), pk=pk)
        return self.run(request, lambda: services.delete_investment(inv, by=request.user))


class HoldingsView(_CapView):
    def post(self, request, org_slug):
        def do():
            d = request.data
            services.add_holding(
                request.org,
                quantity=d.get("quantity"),
                stock_class=d.get("stock_class", services.DEFAULT_CLASS_NAME),
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
            services.update_holding(h, by=request.user, **data)

        return self.run(request, do)

    def delete(self, request, org_slug, pk):
        h = get_object_or_404(StockHolding.objects.for_org(request.org), pk=pk)
        return self.run(request, lambda: services.delete_holding(h, by=request.user))


def _pick(data, fields):
    return {k: data[k] for k in fields if k in data}


class StockClassesView(_CapView):
    def answer(self, request, status=200):
        classes = services.stock_classes(request.org)
        return Response({"stock_classes": [stock_class_dict(c) for c in classes]}, status=status)

    def get(self, request, org_slug):
        return self.answer(request)

    def post(self, request, org_slug):
        data = _pick(request.data, services.STOCK_CLASS_FIELDS)
        return self.run(
            request,
            lambda: services.add_stock_class(request.org, by=request.user, **data),
            answer=lambda: self.answer(request, 201),
        )


class StockClassView(StockClassesView):
    def patch(self, request, org_slug, pk):
        sc = get_object_or_404(StockClass.objects.for_org(request.org), pk=pk)
        data = _pick(request.data, services.STOCK_CLASS_FIELDS)
        return self.run(
            request,
            lambda: services.update_stock_class(sc, by=request.user, **data),
            answer=lambda: self.answer(request),
        )

    def delete(self, request, org_slug, pk):
        sc = get_object_or_404(StockClass.objects.for_org(request.org), pk=pk)
        return self.run(
            request,
            lambda: services.delete_stock_class(sc, by=request.user),
            answer=lambda: self.answer(request),
        )


class ValuationsView(_CapView):
    def answer(self, request, status=200):
        rows = Valuation.objects.for_org(request.org).select_related("stock_class")
        return Response({"valuations": [valuation_dict(v) for v in rows]}, status=status)

    def get(self, request, org_slug):
        return self.answer(request)

    def post(self, request, org_slug):
        data = _pick(request.data, services.VALUATION_FIELDS)
        return self.run(
            request,
            lambda: services.add_valuation(request.org, by=request.user, **data),
            answer=lambda: self.answer(request, 201),
        )


class ValuationView(ValuationsView):
    def patch(self, request, org_slug, pk):
        v = get_object_or_404(Valuation.objects.for_org(request.org), pk=pk)
        data = _pick(request.data, services.VALUATION_FIELDS)
        return self.run(
            request,
            lambda: services.update_valuation(v, by=request.user, **data),
            answer=lambda: self.answer(request),
        )

    def delete(self, request, org_slug, pk):
        v = get_object_or_404(Valuation.objects.for_org(request.org), pk=pk)
        return self.run(
            request,
            lambda: services.delete_valuation(v, by=request.user),
            answer=lambda: self.answer(request),
        )


def _year(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return timezone.localdate().year - 1


class FranchiseTaxView(_CapView):
    """Defaults to last year: the March 1 filing is for the year before."""

    def get(self, request, org_slug):
        ft = services.franchise_tax(request.org, _year(request.query_params.get("year")))
        return Response(franchise_dict(ft))

    def patch(self, request, org_slug):
        year = _year(request.data.get("year"))
        data = _pick(request.data, ("gross_assets", "issued_shares"))
        return self.run(
            request,
            lambda: services.set_franchise_tax_inputs(request.org, year, by=request.user, **data),
            answer=lambda: Response(franchise_dict(services.franchise_tax(request.org, year))),
        )


class EventsView(_CapView):
    def get(self, request, org_slug):
        try:
            limit = max(1, min(int(request.query_params.get("limit", 50)), 500))
        except ValueError:
            limit = 50
        return Response({"events": [event_dict(e) for e in services.events(request.org, limit)]})


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
    path(ORG + "stock-classes/", StockClassesView.as_view(), name="captable-stock-classes"),
    path(ORG + "stock-classes/<int:pk>/", StockClassView.as_view(), name="captable-stock-class"),
    path(ORG + "valuations/", ValuationsView.as_view(), name="captable-valuations"),
    path(ORG + "valuations/<int:pk>/", ValuationView.as_view(), name="captable-valuation"),
    path(ORG + "franchise-tax/", FranchiseTaxView.as_view(), name="captable-franchise-tax"),
    path(ORG + "events/", EventsView.as_view(), name="captable-events"),
    path(ORG + "ocf/", OcfView.as_view(), name="captable-ocf"),
]
