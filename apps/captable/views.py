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

from . import services
from .api import is_cap_admin
from .models import Instrument, Investment, Round, StockHolding


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
        "round_objs": list(Round.objects.for_org(org)),
    }
    return render(request, "captable/index.html", context)


def _back(org_slug, anchor=""):
    resp = redirect("captable:index", org_slug=org_slug)
    if anchor:
        resp["Location"] += f"#{anchor}"
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
    )
    services.set_settings(request.org, **{k: p[k] for k in fields if k in p})
    return "pool" if "founders_pool_pct" in p else "entity"


def _round_data(p):
    return {k: p[k] for k in services.ROUND_FIELDS if k in p}


@_admin_action
def add_round(request):
    services.add_round(request.org, by=request.user, **_round_data(request.POST))
    return "rounds"


@_admin_action
def save_round(request, pk):
    rnd = get_object_or_404(Round.objects.for_org(request.org), pk=pk)
    services.update_round(rnd, **_round_data(request.POST))
    return "rounds"


@_admin_action
def delete_round(request, pk):
    services.delete_round(get_object_or_404(Round.objects.for_org(request.org), pk=pk))
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
    services.update_investment(inv, **data)
    return "investors"


@_admin_action
def delete_investment(request, pk):
    get_object_or_404(Investment.objects.for_org(request.org), pk=pk).delete()
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
        stock_class=p.get("stock_class", "Common"),
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
        h, **{k: p[k] for k in ("quantity", "stock_class", "date", "note") if k in p}
    )
    return "stock"


@_admin_action
def delete_holding(request, pk):
    get_object_or_404(StockHolding.objects.for_org(request.org), pk=pk).delete()
    return "stock"
