"""
Cap table computation and the writes that keep it coherent.

    company = founders pool (pool %)  +  investor tranche (100 − pool %)

* Founders pool: one line per pie slice, percent of company = pie share × pool %. The
  pie is read from apps.pie.services.compute_pie and never recomputed here, so a sponsor
  stake already in the pie (the Workers.VC 3%) shows up inside the pool as it is.
* Investor tranche: one line per investment, percent = amount ÷ the round's post-money
  valuation. What the tranche has not sold is one more line, "not yet sold".
* Value of a line = its percent × the latest round's post-money valuation.

The lines always sum to 100%. Every write that could break that (a bigger pool, a
bigger investment, a lower valuation) runs inside a transaction that recomputes the
table and refuses — rolling back — if investors would hold more than their tranche.

Stock holdings (the legal shares of record) are listed alongside, never added in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional

from django.db import transaction
from django.utils.dateparse import parse_date
from django.utils.text import slugify

from apps.orgs.models import ExternalHolder, Membership, StakeholderType
from apps.pie.services import HOLDER_SPONSOR, compute_pie

from .models import (
    DEFAULT_FOUNDERS_POOL_PCT,
    CapTableSettings,
    Instrument,
    Investment,
    Round,
    StockHolding,
)

ZERO = Decimal("0")
HUNDRED = Decimal("100")
PCT_Q = Decimal("0.01")
CENTS = Decimal("0.01")

KIND_FOUNDER = "founder"
KIND_SPONSOR = "sponsor"
KIND_INVESTOR = "investor"
KIND_UNSOLD = "unsold"
# The pool before anyone has earned into it: still the founders', just not anyone's yet.
KIND_POOL_UNEARNED = "pool_unearned"


class CapTableError(ValueError):
    """A write that would leave the cap table incoherent."""


# --------------------------------------------------------------------------- #
# Result shapes.
# --------------------------------------------------------------------------- #
@dataclass
class CapLine:
    kind: str
    label: str
    pct: Decimal  # percent of the company, full precision
    value: Optional[Decimal]  # pct × latest post-money; None until a round is valued
    membership_id: Optional[int] = None
    holder_id: Optional[int] = None
    holder_slug: str = ""
    # Founder lines: the pie behind the number.
    pie_share_pct: Optional[Decimal] = None
    pie_total: Optional[Decimal] = None
    # Investor lines: the investment behind the number.
    investment_id: Optional[int] = None
    round_id: Optional[int] = None
    round_name: str = ""
    instrument: str = ""
    amount: Optional[Decimal] = None
    date: Optional[object] = None
    note: str = ""

    @property
    def pct_display(self) -> Decimal:
        return self.pct.quantize(PCT_Q)


@dataclass
class RoundSummary:
    round_id: int
    name: str
    date: object
    instrument: str
    raised: Decimal
    pre_money: Optional[Decimal]
    post_money: Optional[Decimal]
    price_per_share: Optional[Decimal]
    sold_pct: Decimal
    investment_count: int
    # What was typed, as opposed to derived: the edit form shows these.
    stated_pre: Optional[Decimal] = None
    stated_post: Optional[Decimal] = None


@dataclass
class HoldingLine:
    holding_id: int
    label: str
    membership_id: Optional[int]
    holder_id: Optional[int]
    stock_class: str
    quantity: int
    pct_of_shares: Decimal
    date: Optional[object]
    note: str


@dataclass
class CapTable:
    org_slug: str
    currency: str
    unit_name: str
    founders_pool_pct: Decimal
    investor_pool_pct: Decimal
    sold_pct: Decimal
    unsold_pct: Decimal
    latest_post_money: Optional[Decimal]
    latest_round_name: str
    total_pct: Decimal
    total_value: Optional[Decimal]
    lines: List[CapLine] = field(default_factory=list)
    rounds: List[RoundSummary] = field(default_factory=list)
    holdings: List[HoldingLine] = field(default_factory=list)
    shares_of_record: int = 0


# --------------------------------------------------------------------------- #
# Reads.
# --------------------------------------------------------------------------- #
def get_settings(org) -> CapTableSettings:
    """The org's settings row, or an unsaved one carrying the defaults (reads never write)."""
    found = CapTableSettings.objects.filter(org=org).first()
    return found or CapTableSettings(org=org, founders_pool_pct=DEFAULT_FOUNDERS_POOL_PCT)


def round_valuations(rnd: Round, raised: Decimal):
    """(pre, post) for a round. Whichever was not stated is derived from what it raised."""
    pre, post = rnd.pre_money_valuation, rnd.post_money_valuation
    if post is None and pre is not None:
        post = pre + raised
    if pre is None and post is not None:
        pre = post - raised
    return pre, post


def _value(pct: Decimal, post: Optional[Decimal]) -> Optional[Decimal]:
    if post is None:
        return None
    return (pct / HUNDRED * post).quantize(CENTS)


def compute_cap_table(org) -> CapTable:
    """The whole company as lines that sum to 100%, each valued at the latest round."""
    cfg = get_settings(org)
    pool_pct = cfg.founders_pool_pct
    investor_pct = HUNDRED - pool_pct

    rounds = list(Round.objects.for_org(org).prefetch_related("investments"))
    investments = list(
        Investment.objects.for_org(org).select_related("round", "membership__user", "holder")
    )
    by_round = {}
    for inv in investments:
        by_round.setdefault(inv.round_id, []).append(inv)

    summaries: List[RoundSummary] = []
    post_by_round = {}
    for rnd in rounds:
        invs = by_round.get(rnd.pk, [])
        raised = sum((i.amount for i in invs), ZERO)
        pre, post = round_valuations(rnd, raised)
        post_by_round[rnd.pk] = post
        sold = (raised / post * HUNDRED) if post and post > ZERO else ZERO
        summaries.append(
            RoundSummary(
                round_id=rnd.pk,
                name=rnd.name,
                date=rnd.date,
                instrument=rnd.instrument,
                raised=raised,
                pre_money=pre,
                post_money=post,
                price_per_share=rnd.price_per_share,
                sold_pct=sold,
                investment_count=len(invs),
                stated_pre=rnd.pre_money_valuation,
                stated_post=rnd.post_money_valuation,
            )
        )

    latest = next((s for s in reversed(summaries) if s.post_money and s.post_money > ZERO), None)
    latest_post = latest.post_money if latest else None

    lines: List[CapLine] = []

    # Founders pool: the pie, scaled into the pool.
    pie = compute_pie(org)
    if pie.total > ZERO:
        for s in pie.slices:
            if s.issued_total == ZERO:
                continue
            frac = s.issued_total / pie.total
            pct = frac * pool_pct
            lines.append(
                CapLine(
                    kind=KIND_SPONSOR if s.holder_kind == HOLDER_SPONSOR else KIND_FOUNDER,
                    label=s.member_label,
                    pct=pct,
                    value=_value(pct, latest_post),
                    membership_id=s.membership_id,
                    holder_slug=s.holder_slug,
                    pie_share_pct=(frac * HUNDRED).quantize(PCT_Q),
                    pie_total=s.issued_total,
                )
            )
    elif pool_pct > ZERO:
        lines.append(
            CapLine(
                kind=KIND_POOL_UNEARNED,
                label="Founders pool",
                pct=pool_pct,
                value=_value(pool_pct, latest_post),
            )
        )

    # Investor tranche: each investment at its own round's post-money.
    sold = ZERO
    for inv in investments:
        post = post_by_round.get(inv.round_id)
        pct = (inv.amount / post * HUNDRED) if post and post > ZERO else ZERO
        sold += pct
        lines.append(
            CapLine(
                kind=KIND_INVESTOR,
                label=inv.holder_label,
                pct=pct,
                value=_value(pct, latest_post),
                membership_id=inv.membership_id,
                holder_id=inv.holder_id,
                holder_slug=inv.holder.slug if inv.holder_id else "",
                investment_id=inv.pk,
                round_id=inv.round_id,
                round_name=inv.round.name,
                instrument=inv.round.instrument,
                amount=inv.amount,
                date=inv.date or inv.round.date,
                note=inv.note,
            )
        )

    unsold = investor_pct - sold
    if unsold > ZERO:
        lines.append(
            CapLine(
                kind=KIND_UNSOLD,
                label="Not yet sold",
                pct=unsold,
                value=_value(unsold, latest_post),
            )
        )

    total_pct = sum((ln.pct for ln in lines), ZERO)
    total_value = sum((ln.value for ln in lines), ZERO) if latest_post is not None else None

    holdings_qs = list(
        StockHolding.objects.for_org(org).select_related("membership__user", "holder")
    )
    shares = sum((h.quantity for h in holdings_qs), 0)
    holdings = [
        HoldingLine(
            holding_id=h.pk,
            label=h.holder_label,
            membership_id=h.membership_id,
            holder_id=h.holder_id,
            stock_class=h.stock_class,
            quantity=h.quantity,
            pct_of_shares=(
                (Decimal(h.quantity) / Decimal(shares) * HUNDRED).quantize(PCT_Q)
                if shares
                else ZERO
            ),
            date=h.date,
            note=h.note,
        )
        for h in holdings_qs
    ]

    return CapTable(
        org_slug=org.slug,
        currency=cfg.currency,
        unit_name=org.unit_name,
        founders_pool_pct=pool_pct,
        investor_pool_pct=investor_pct,
        sold_pct=sold,
        unsold_pct=max(unsold, ZERO),
        latest_post_money=latest_post,
        latest_round_name=latest.name if latest else "",
        total_pct=total_pct.quantize(PCT_Q),
        total_value=total_value,
        lines=lines,
        rounds=summaries,
        holdings=holdings,
        shares_of_record=shares,
    )


def holder_choices(org):
    """Who can hold a line here: the org's members, then outside holders already on it."""
    members = [
        {"key": f"m:{m.pk}", "label": m.user.get_short_name(), "kind": "member"}
        for m in Membership.objects.filter(org=org).select_related("user").order_by("id")
    ]
    ids = set(
        Investment.objects.for_org(org)
        .filter(holder__isnull=False)
        .values_list("holder_id", flat=True)
    ) | set(
        StockHolding.objects.for_org(org)
        .filter(holder__isnull=False)
        .values_list("holder_id", flat=True)
    )
    outside = [
        {"key": f"h:{h.pk}", "label": h.display_name, "kind": h.stakeholder_type}
        for h in ExternalHolder.objects.filter(pk__in=ids)
    ]
    return members + outside


# --------------------------------------------------------------------------- #
# Writes. Each one is atomic and re-checks the whole table before it commits.
# --------------------------------------------------------------------------- #
def _check(org):
    table = compute_cap_table(org)
    if table.sold_pct > table.investor_pool_pct:
        raise CapTableError(
            f"Investors would hold {table.sold_pct.quantize(PCT_Q)}% of the company; "
            f"their tranche is {table.investor_pool_pct}%."
        )
    return table


def _pct(value) -> Decimal:
    pct = Decimal(str(value))
    if pct < ZERO or pct > HUNDRED:
        raise CapTableError("A percent is between 0 and 100.")
    return pct.quantize(PCT_Q)


@transaction.atomic
def set_settings(org, founders_pool_pct=None, currency=None, **issuer) -> CapTableSettings:
    cfg, _ = CapTableSettings.objects.get_or_create(org=org)
    if founders_pool_pct not in (None, ""):
        cfg.founders_pool_pct = _pct(founders_pool_pct)
    if currency:
        cfg.currency = str(currency).strip().upper()[:3]
    if "legal_name" in issuer:
        cfg.legal_name = (issuer["legal_name"] or "").strip()[:255]
    if "formation_date" in issuer:
        cfg.formation_date = _date(issuer["formation_date"])
    if "country_of_formation" in issuer:
        cfg.country_of_formation = (issuer["country_of_formation"] or "").strip().upper()[:2]
    if "country_subdivision_of_formation" in issuer:
        cfg.country_subdivision_of_formation = (
            (issuer["country_subdivision_of_formation"] or "").strip().upper()[:6]
        )
    cfg.save()
    _check(org)
    return cfg


def resolve_holder(org, key: str = "", new_name: str = "", new_type: str = ""):
    """(membership, holder) from a holder key ("m:<id>" or "h:<id>"), or a new outside one.

    A new name always makes a new ExternalHolder: two people with the same name in two
    ventures are two people, so a name is never matched onto someone already there.
    """
    if key:
        kind, _, pk = key.partition(":")
        if kind == "m":
            m = Membership.objects.filter(org=org, pk=pk).first()
            if m is None:
                raise CapTableError("That person is not a member here.")
            return m, None
        if kind == "h":
            h = ExternalHolder.objects.filter(pk=pk).first()
            if h is None:
                raise CapTableError("No such holder.")
            return None, h
    name = (new_name or "").strip()
    if not name:
        raise CapTableError("Pick who holds it, or name them.")
    stype = new_type if new_type in StakeholderType.values else StakeholderType.INDIVIDUAL
    base = slugify(name)[:56] or "holder"
    slug, n = base, 1
    while ExternalHolder.objects.filter(slug=slug).exists():
        n += 1
        slug = f"{base}-{n}"
    return None, ExternalHolder.objects.create(slug=slug, display_name=name, stakeholder_type=stype)


def _date(value):
    """A date from a date, an ISO string, or blank (None)."""
    if value in ("", None):
        return None
    if isinstance(value, str):
        parsed = parse_date(value)
        if parsed is None:
            raise CapTableError("A date is written YYYY-MM-DD.")
        return parsed
    return value


ROUND_FIELDS = (
    "name",
    "date",
    "instrument",
    "pre_money_valuation",
    "post_money_valuation",
    "price_per_share",
)


def _round_fields(data: dict) -> dict:
    out = {}
    for f in ROUND_FIELDS:
        if f in data:
            v = data[f]
            out[f] = None if v in ("", None) and f not in ("name", "date", "instrument") else v
    if "date" in out:
        out["date"] = _date(out["date"])
        if out["date"] is None:
            raise CapTableError("A round needs a date.")
    if "instrument" in out and out["instrument"] not in Instrument.values:
        raise CapTableError("A round is a SAFE or priced.")
    if "name" in out and not str(out["name"]).strip():
        raise CapTableError("A round needs a name.")
    for f in ("pre_money_valuation", "post_money_valuation", "price_per_share"):
        if out.get(f) is not None:
            out[f] = Decimal(str(out[f]))
            if out[f] <= ZERO:
                raise CapTableError("A valuation or price is above zero.")
    return out


def _require_valuation(rnd: Round):
    if rnd.pre_money_valuation is None and rnd.post_money_valuation is None:
        raise CapTableError("A round needs a pre-money or a post-money valuation.")


@transaction.atomic
def add_round(org, by=None, **data) -> Round:
    fields = _round_fields(data)
    if not fields.get("date"):
        raise CapTableError("A round needs a date.")
    rnd = Round(org=org, created_by=by, **fields)
    _require_valuation(rnd)
    rnd.save()
    _check(org)
    return rnd


@transaction.atomic
def update_round(rnd: Round, **data) -> Round:
    for k, v in _round_fields(data).items():
        setattr(rnd, k, v)
    _require_valuation(rnd)
    rnd.save()
    _check(rnd.org)
    return rnd


@transaction.atomic
def delete_round(rnd: Round):
    if rnd.investments.exists():
        raise CapTableError("Remove this round's investments first.")
    rnd.delete()


def _money(value) -> Decimal:
    amount = Decimal(str(value))
    if amount <= ZERO:
        raise CapTableError("An amount is above zero.")
    return amount.quantize(CENTS)


@transaction.atomic
def add_investment(
    org, rnd: Round, membership=None, holder=None, amount=None, date=None, note="", by=None
):
    if rnd.org_id != org.pk:
        raise CapTableError("That round is not in this org.")
    inv = Investment.objects.create(
        org=org,
        round=rnd,
        membership=membership,
        holder=holder,
        amount=_money(amount),
        date=_date(date),
        note=note or "",
        created_by=by,
    )
    _check(org)
    return inv


@transaction.atomic
def update_investment(inv: Investment, **data) -> Investment:
    if "amount" in data:
        inv.amount = _money(data["amount"])
    if "date" in data:
        inv.date = _date(data["date"])
    if "note" in data:
        inv.note = data["note"] or ""
    if "round" in data:
        if data["round"].org_id != inv.org_id:
            raise CapTableError("That round is not in this org.")
        inv.round = data["round"]
    if "membership" in data or "holder" in data:
        inv.membership, inv.holder = data.get("membership"), data.get("holder")
    inv.save()
    _check(inv.org)
    return inv


@transaction.atomic
def add_holding(
    org,
    membership=None,
    holder=None,
    quantity=None,
    stock_class="Common",
    date=None,
    note="",
    by=None,
):
    qty = int(quantity or 0)
    if qty <= 0:
        raise CapTableError("A share count is above zero.")
    return StockHolding.objects.create(
        org=org,
        membership=membership,
        holder=holder,
        quantity=qty,
        stock_class=(stock_class or "Common").strip()[:80],
        date=_date(date),
        note=note or "",
        created_by=by,
    )


@transaction.atomic
def update_holding(h: StockHolding, **data) -> StockHolding:
    if "quantity" in data:
        qty = int(data["quantity"] or 0)
        if qty <= 0:
            raise CapTableError("A share count is above zero.")
        h.quantity = qty
    if "stock_class" in data:
        h.stock_class = (data["stock_class"] or "Common").strip()[:80]
    if "date" in data:
        h.date = _date(data["date"])
    if "note" in data:
        h.note = data["note"] or ""
    if "membership" in data or "holder" in data:
        h.membership, h.holder = data.get("membership"), data.get("holder")
    h.save()
    return h
