"""
The cap table page. Members read it; admins edit every line where it sits.

Every form posts to one of the small handlers below, each a thin call into
apps.captable.services (the same calls the API makes), then back to the page.
"""

from decimal import InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.commons import storage

from . import services
from .templatetags.captable_fmt import as_pct, money, plain
from .api import is_cap_admin
from django.utils import timezone

from .models import (
    POOL_DILUTION_CHOICES,
    CompanyDocument,
    DocumentKind,
    ConversionTiming,
    EntityType,
    Instrument,
    Investment,
    Round,
    Scope,
    StockClass,
    StockClassType,
    StockHolding,
    Valuation,
    ValuationType,
)

HISTORY_LINES = 8
# Where on the page each kind of change can be seen and edited.
HISTORY_ANCHORS = {
    "settings": "entity",
    "round": "rounds",
    "investment": "investors",
    "holding": "stock",
    "stock_class": "classes",
    "valuation": "valuations",
    "franchise_tax_year": "franchise-tax",
    "document": "documents",
}


@login_required
def index(request, org_slug):
    org = request.org
    table = services.compute_cap_table(org)
    lines = table.lines
    pool = [
        ln
        for ln in lines
        if ln.kind in (services.KIND_FOUNDER, services.KIND_SPONSOR, services.KIND_POOL_UNEARNED)
    ]
    investors = [ln for ln in lines if ln.kind == services.KIND_INVESTOR]
    unsold = next((ln for ln in lines if ln.kind == services.KIND_UNSOLD), None)
    priced = table.latest_post_money is not None

    round_objs = list(Round.objects.for_org(org))
    cfg = services.get_settings(org)
    franchise = franchise_years = None
    if cfg.country_subdivision_of_formation == "DE":
        last = timezone.localdate().year - 1
        try:
            year = int(request.GET.get("tax_year") or last)
        except ValueError:
            year = last
        franchise = services.franchise_tax(org, year)
        franchise_years = list(range(last + 1, last - 5, -1))

    def subtotal(rows):
        return sum((r.value for r in rows), services.ZERO) if priced else None

    context = {
        "page_title": "Cap table",
        "org_slug": org_slug,
        "table": table,
        "pool_lines": pool,
        "pool_value": subtotal(pool),
        "investor_lines": investors,
        "investor_value": subtotal(investors + ([unsold] if unsold else [])),
        "unsold": unsold,
        "is_admin": is_cap_admin(request),
        "settings": services.get_settings(org),
        "holders": services.holder_choices(org),
        "instruments": Instrument.choices,
        "round_objs": round_objs,
        "terms": {r.pk: r for r in round_objs},
        "terms_text": {r.pk: _terms_text(r, table.currency) for r in round_objs},
        "conversion_timings": ConversionTiming.choices,
        "stock_classes": services.stock_classes(org),
        "class_types": StockClassType.choices,
        "entity_types": EntityType.choices,
        "scopes": Scope.choices,
        "pool_dilutions": POOL_DILUTION_CHOICES,
        "pool_only": table.scope == Scope.POOL_ONLY,
        "outside": next((ln for ln in lines if ln.kind == services.KIND_OUTSIDE), None),
        "valuations": list(Valuation.objects.for_org(org).select_related("stock_class")),
        "valuation_types": ValuationType.choices,
        "documents": services.documents(org),
        "document_kinds": DocumentKind.choices,
        "uploads_on": storage.configured(),
        "franchise": franchise,
        "franchise_years": franchise_years,
        "history": [
            (e, HISTORY_ANCHORS.get(e.object_type, "")) for e in services.events(org, HISTORY_LINES)
        ],
    }
    return render(request, "captable/index.html", context)


def _terms_text(rnd, currency):
    """A round's terms as one line for a reader: "$4,000,000 cap · 20% discount · MFN"."""
    parts = []
    if rnd.instrument == Instrument.PRICED and rnd.price_per_share:
        parts.append(f"{plain(rnd.price_per_share)} {currency} per share")
    if rnd.conversion_valuation_cap:
        parts.append(f"{money(rnd.conversion_valuation_cap, currency)} cap")
    if rnd.conversion_discount is not None:
        parts.append(f"{as_pct(rnd.conversion_discount)}% discount")
    if rnd.instrument == Instrument.SAFE:
        parts.append(rnd.get_conversion_timing_display())
    if rnd.interest_rate is not None:
        parts.append(f"{as_pct(rnd.interest_rate)}% interest")
    if rnd.maturity_date:
        parts.append(f"matures {rnd.maturity_date.isoformat()}")
    if rnd.conversion_mfn:
        parts.append("MFN")
    if rnd.pro_rata:
        parts.append("pro rata")
    return " · ".join(parts)


def _back(org_slug, anchor=""):
    """Back to the page at ``anchor``: "section", or "?query#section"."""
    resp = redirect("captable:index", org_slug=org_slug)
    if anchor:
        resp["Location"] += anchor if anchor.startswith("?") else f"#{anchor}"
    return resp


def _admin_action(fn):
    """POST-only, admin-only; a refused write comes back as one message, nothing saved."""

    @login_required
    @require_POST
    def wrapped(request, org_slug, *args, **kwargs):
        if not is_cap_admin(request):
            messages.error(request, "Only an admin can change the cap table.")
            return _back(org_slug)
        try:
            anchor = fn(request, *args, **kwargs) or ""
        except services.CapTableError as exc:
            messages.error(request, str(exc))
            return _back(org_slug)
        except (InvalidOperation, ValueError, TypeError):
            messages.error(request, "That is not a number.")
            return _back(org_slug)
        return _back(org_slug, anchor)

    return wrapped


def _holder(request):
    p = request.POST
    return services.resolve_holder(
        request.org,
        key=p.get("holder", ""),
        new_name=p.get("holder_name", ""),
        new_type=p.get("holder_type", ""),
    )


@_admin_action
def save_settings(request):
    p = request.POST
    fields = (
        "founders_pool_pct",
        "currency",
        "legal_name",
        "formation_date",
        "country_of_formation",
        "country_subdivision_of_formation",
        "entity_type",
        "incorporated",
        "scope",
        "pool_dilutes_at_priced_round",
    )
    services.set_settings(request.org, by=request.user, **{k: p[k] for k in fields if k in p})
    return "pool" if "founders_pool_pct" in p or "scope" in p else "entity"


def _round_data(p):
    return {k: p[k] for k in services.ROUND_INPUTS if k in p}


@_admin_action
def add_round(request):
    services.add_round(request.org, by=request.user, **_round_data(request.POST))
    return "rounds"


@_admin_action
def save_round(request, pk):
    rnd = get_object_or_404(Round.objects.for_org(request.org), pk=pk)
    services.update_round(rnd, by=request.user, **_round_data(request.POST))
    return "rounds"


@_admin_action
def delete_round(request, pk):
    services.delete_round(
        get_object_or_404(Round.objects.for_org(request.org), pk=pk), by=request.user
    )
    return "rounds"


@_admin_action
def add_investment(request):
    p = request.POST
    rnd = get_object_or_404(Round.objects.for_org(request.org), pk=p.get("round") or 0)
    m, h = _holder(request)
    services.add_investment(
        request.org,
        rnd,
        membership=m,
        holder=h,
        amount=p.get("amount"),
        date=p.get("date"),
        note=p.get("note", ""),
        by=request.user,
    )
    return "investors"


@_admin_action
def save_investment(request, pk):
    inv = get_object_or_404(Investment.objects.for_org(request.org), pk=pk)
    p = request.POST
    data = {k: p[k] for k in ("amount", "date", "note") if k in p}
    if p.get("round"):
        data["round"] = get_object_or_404(Round.objects.for_org(request.org), pk=p["round"])
    services.update_investment(inv, by=request.user, **data)
    return "investors"


@_admin_action
def delete_investment(request, pk):
    services.delete_investment(
        get_object_or_404(Investment.objects.for_org(request.org), pk=pk), by=request.user
    )
    return "investors"


@_admin_action
def add_holding(request):
    p = request.POST
    m, h = _holder(request)
    services.add_holding(
        request.org,
        membership=m,
        holder=h,
        quantity=p.get("quantity"),
        stock_class=p.get("stock_class", services.DEFAULT_CLASS_NAME),
        date=p.get("date"),
        note=p.get("note", ""),
        by=request.user,
    )
    return "stock"


@_admin_action
def save_holding(request, pk):
    h = get_object_or_404(StockHolding.objects.for_org(request.org), pk=pk)
    p = request.POST
    services.update_holding(
        h,
        by=request.user,
        **{k: p[k] for k in ("quantity", "stock_class", "date", "note") if k in p},
    )
    return "stock"


@_admin_action
def delete_holding(request, pk):
    services.delete_holding(
        get_object_or_404(StockHolding.objects.for_org(request.org), pk=pk), by=request.user
    )
    return "stock"


def _fields(p, names):
    return {k: p[k] for k in names if k in p}


@_admin_action
def add_stock_class(request):
    services.add_stock_class(
        request.org, by=request.user, **_fields(request.POST, services.STOCK_CLASS_FIELDS)
    )
    return "classes"


@_admin_action
def save_stock_class(request, pk):
    sc = get_object_or_404(StockClass.objects.for_org(request.org), pk=pk)
    services.update_stock_class(
        sc, by=request.user, **_fields(request.POST, services.STOCK_CLASS_FIELDS)
    )
    return "classes"


@_admin_action
def delete_stock_class(request, pk):
    services.delete_stock_class(
        get_object_or_404(StockClass.objects.for_org(request.org), pk=pk), by=request.user
    )
    return "classes"


@_admin_action
def add_valuation(request):
    services.add_valuation(
        request.org, by=request.user, **_fields(request.POST, services.VALUATION_FIELDS)
    )
    return "valuations"


@_admin_action
def save_valuation(request, pk):
    v = get_object_or_404(Valuation.objects.for_org(request.org), pk=pk)
    services.update_valuation(
        v, by=request.user, **_fields(request.POST, services.VALUATION_FIELDS)
    )
    return "valuations"


@_admin_action
def delete_valuation(request, pk):
    services.delete_valuation(
        get_object_or_404(Valuation.objects.for_org(request.org), pk=pk), by=request.user
    )
    return "valuations"


@_admin_action
def add_document(request):
    p = request.POST
    services.add_document(
        request.org,
        kind=p.get("kind", ""),
        title=p.get("title", ""),
        url=p.get("url", ""),
        upload=request.FILES.get("file"),
        by=request.user,
    )
    return "documents"


@_admin_action
def delete_document(request, pk):
    services.delete_document(
        get_object_or_404(CompanyDocument.objects.for_org(request.org), pk=pk), by=request.user
    )
    return "documents"


@_admin_action
def save_franchise_tax(request):
    p = request.POST
    row = services.set_franchise_tax_inputs(
        request.org,
        p.get("year"),
        by=request.user,
        **_fields(p, ("gross_assets", "issued_shares")),
    )
    return f"?tax_year={row.year}#franchise-tax"
