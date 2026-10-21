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

Convertibles before they convert. Every SAFE and note line uses the same math as a
post-money SAFE: amount ÷ its round's post-money valuation, where the post-money is
stated or is pre-money + what the round raised. So:

* post-money SAFE: state the post-money valuation (the cap); the line is exact.
* pre-money SAFE: state the pre-money valuation (the cap); the line is the holder's
  share as if it converted at the cap with only this round's money in. The real share
  is set at the next priced round and is usually a little lower.
* convertible note: like a pre-money SAFE, on the principal only; accrued interest and
  the discount show only once the note converts.

Scope. ``WHOLE_COMPANY`` (the default) is the table above. ``POOL_ONLY`` is for a
company whose cap table is kept somewhere else and puts only the founders pool here:
the pool lines, then one line "Held outside this record" for the rest. Investments
already recorded stay recorded and in the export; they leave the lines.

Every add, change and removal below writes one CapTableEvent in its own transaction.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import List, Optional

from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.core.validators import URLValidator
from django.db import transaction
from django.forms.models import model_to_dict
from django.utils.dateparse import parse_date
from django.utils.text import slugify

from apps.commons import storage
from apps.orgs.models import ExternalHolder, Membership, StakeholderType
from apps.pie.services import HOLDER_SPONSOR, compute_pie

from .models import (
    DEFAULT_FOUNDERS_POOL_PCT,
    CapTableEvent,
    CapTableSettings,
    CompanyDocument,
    ConversionTiming,
    DocumentKind,
    EntityType,
    EventKind,
    FranchiseTaxYear,
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

logger = logging.getLogger(__name__)

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
# POOL_ONLY scope: everything outside the founders pool, kept in another record.
KIND_OUTSIDE = "outside"
DEFAULT_CLASS_NAME = "Common"


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
    stock_class_id: int
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
    scope: str = Scope.WHOLE_COMPANY


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
    pool_only = cfg.scope == Scope.POOL_ONLY
    sold = ZERO
    for inv in investments:
        post = post_by_round.get(inv.round_id)
        pct = (inv.amount / post * HUNDRED) if post and post > ZERO else ZERO
        sold += pct
        if pool_only:
            continue
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
    if pool_only:
        if investor_pct > ZERO:
            lines.append(
                CapLine(
                    kind=KIND_OUTSIDE,
                    label="Held outside this record",
                    pct=investor_pct,
                    value=_value(investor_pct, latest_post),
                )
            )
    elif unsold > ZERO:
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
        StockHolding.objects.for_org(org).select_related(
            "membership__user", "holder", "stock_class"
        )
    )
    shares = sum((h.quantity for h in holdings_qs), 0)
    holdings = [
        HoldingLine(
            holding_id=h.pk,
            label=h.holder_label,
            membership_id=h.membership_id,
            holder_id=h.holder_id,
            stock_class=h.stock_class.name,
            stock_class_id=h.stock_class_id,
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
        scope=cfg.scope,
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


def _bool(value) -> bool:
    """A checkbox, a JSON boolean, or the text of either."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "on", "yes")


# --------------------------------------------------------------------------- #
# The audit trail: one CapTableEvent per add, change, removal.
# --------------------------------------------------------------------------- #
OBJECT_TYPES = {
    CapTableSettings: "settings",
    Round: "round",
    Investment: "investment",
    StockHolding: "holding",
    StockClass: "stock_class",
    Valuation: "valuation",
    FranchiseTaxYear: "franchise_tax_year",
    CompanyDocument: "document",
}
_SNAP_SKIP = ("org", "created_by", "uploaded_by")


def _snap(obj) -> dict:
    """The row as JSON. Decimals at their column's precision, so a value read back from
    the database and the same value just typed compare equal."""
    data = {k: v for k, v in model_to_dict(obj).items() if k not in _SNAP_SKIP}
    for f in obj._meta.concrete_fields:
        if f.name in data and data[f.name] is not None and f.get_internal_type() == "DecimalField":
            data[f.name] = Decimal(data[f.name]).quantize(Decimal(1).scaleb(-f.decimal_places))
    return json.loads(json.dumps(data, cls=DjangoJSONEncoder))


def _label(obj) -> str:
    if isinstance(obj, CapTableSettings):
        return "Settings"
    if isinstance(obj, (Investment, StockHolding)):
        return obj.holder_label
    if isinstance(obj, Valuation):
        return f"{obj.stock_class.name} {obj.effective_date}"
    if isinstance(obj, FranchiseTaxYear):
        return str(obj.year)
    if isinstance(obj, CompanyDocument):
        return obj.title
    return getattr(obj, "name", "") or ""


def _record(org, by, kind, obj, before=None, after=None):
    """Append the event. A change that changed nothing writes nothing."""
    if kind == EventKind.UPDATE and before == after:
        return None
    return CapTableEvent.objects.create(
        org=org,
        actor=by if getattr(by, "is_authenticated", False) else None,
        kind=kind,
        object_type=OBJECT_TYPES[type(obj)],
        object_id=obj.pk,
        label=_label(obj)[:255],
        before=before,
        after=after,
    )


def _added(org, by, obj):
    _record(org, by, EventKind.ADD, obj, after=_snap(obj))


def _deleted(org, by, obj):
    before = _snap(obj)
    label_obj = obj
    pk = obj.pk
    obj.delete()
    label_obj.pk = pk
    _record(org, by, EventKind.DELETE, label_obj, before=before)


@dataclass
class EventLine:
    at: object
    actor: str
    kind: str
    object_type: str
    object_id: Optional[int]
    label: str
    changes: list  # [(field, before, after)] for a change; [] for an add or removal
    before: Optional[dict]
    after: Optional[dict]

    @property
    def text(self) -> str:
        what = self.object_type.replace("_", " ")
        head = f"{EventKind(self.kind).label} {what} {self.label}".strip()
        if not self.changes:
            return head
        parts = [
            f"{f.replace('_', ' ')} {'blank' if b in (None, '') else b} → "
            f"{'blank' if a in (None, '') else a}"
            for f, b, a in self.changes
        ]
        return f"{head}: " + "; ".join(parts)


def events(org, limit: int = 50) -> List[EventLine]:
    """Newest first."""
    out = []
    for ev in CapTableEvent.objects.filter(org=org).select_related("actor")[:limit]:
        changes = []
        if ev.kind == EventKind.UPDATE:
            b, a = ev.before or {}, ev.after or {}
            changes = [(k, b.get(k), a.get(k)) for k in a if b.get(k) != a.get(k)]
        out.append(
            EventLine(
                at=ev.at,
                actor=ev.actor.get_short_name() if ev.actor_id else "",
                kind=ev.kind,
                object_type=ev.object_type,
                object_id=ev.object_id,
                label=ev.label,
                changes=changes,
                before=ev.before,
                after=ev.after,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Settings.
# --------------------------------------------------------------------------- #
@transaction.atomic
def set_settings(org, founders_pool_pct=None, currency=None, by=None, **issuer) -> CapTableSettings:
    cfg, _ = CapTableSettings.objects.get_or_create(org=org)
    before = _snap(cfg)
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
    if "entity_type" in issuer:
        et = issuer["entity_type"] or ""
        if et and et not in EntityType.values:
            raise CapTableError("An entity is a C corporation, a PBC, an LLC or other.")
        cfg.entity_type = et
    if "incorporated" in issuer:
        cfg.incorporated = _bool(issuer["incorporated"])
    if "scope" in issuer:
        if issuer["scope"] not in Scope.values:
            raise CapTableError("The cap table covers the whole company or the founders pool.")
        cfg.scope = issuer["scope"]
    if "pool_dilutes_at_priced_round" in issuer:
        cfg.pool_dilutes_at_priced_round = _bool(issuer["pool_dilutes_at_priced_round"])
    cfg.save()
    _record(org, by, EventKind.UPDATE, cfg, before, _snap(cfg))
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


def _fraction(value, what):
    """A fraction 0..1 (OCF Percentage), or None for blank."""
    if value in ("", None):
        return None
    f = Decimal(str(value))
    if f < ZERO or f > 1:
        raise CapTableError(f"A {what} is between 0% and 100%.")
    return f


# --------------------------------------------------------------------------- #
# Rounds.
# --------------------------------------------------------------------------- #
ROUND_FIELDS = (
    "name",
    "date",
    "instrument",
    "pre_money_valuation",
    "post_money_valuation",
    "price_per_share",
    "conversion_valuation_cap",
    "conversion_discount",
    "conversion_timing",
    "conversion_mfn",
    "pro_rata",
    "interest_rate",
    "maturity_date",
)
# The page asks for these two as percents; the record keeps OCF's fractions.
ROUND_PCT_ALIASES = {
    "conversion_discount_pct": "conversion_discount",
    "interest_rate_pct": "interest_rate",
}
ROUND_INPUTS = ROUND_FIELDS + tuple(ROUND_PCT_ALIASES)
_REQUIRED_ROUND = ("name", "date", "instrument", "conversion_timing", "conversion_mfn", "pro_rata")


def _round_fields(data: dict) -> dict:
    data = dict(data)
    for alias, f in ROUND_PCT_ALIASES.items():
        if alias in data:
            v = data.pop(alias)
            data[f] = None if v in ("", None) else Decimal(str(v)) / HUNDRED
    out = {}
    for f in ROUND_FIELDS:
        if f in data:
            v = data[f]
            out[f] = None if v in ("", None) and f not in _REQUIRED_ROUND else v
    if "date" in out:
        out["date"] = _date(out["date"])
        if out["date"] is None:
            raise CapTableError("A round needs a date.")
    if "maturity_date" in out:
        out["maturity_date"] = _date(out["maturity_date"])
    if "instrument" in out and out["instrument"] not in Instrument.values:
        raise CapTableError("A round is a SAFE, a convertible note or priced.")
    if "conversion_timing" in out and out["conversion_timing"] not in ConversionTiming.values:
        raise CapTableError("A SAFE is pre-money or post-money.")
    for f in ("conversion_mfn", "pro_rata"):
        if f in out:
            out[f] = _bool(out[f])
    if "name" in out and not str(out["name"]).strip():
        raise CapTableError("A round needs a name.")
    for f in (
        "pre_money_valuation",
        "post_money_valuation",
        "price_per_share",
        "conversion_valuation_cap",
    ):
        if out.get(f) is not None:
            out[f] = Decimal(str(out[f]))
            if out[f] <= ZERO:
                raise CapTableError("A valuation or price is above zero.")
    if "conversion_discount" in out:
        out["conversion_discount"] = _fraction(out["conversion_discount"], "discount")
    if "interest_rate" in out:
        out["interest_rate"] = _fraction(out["interest_rate"], "rate")
    return out


def _require_valuation(rnd: Round):
    if rnd.pre_money_valuation is None and rnd.post_money_valuation is None:
        raise CapTableError("A round needs a pre-money or a post-money valuation.")


@transaction.atomic
def add_round(org, by=None, **data) -> Round:
    fields = _round_fields(data)
    if not fields.get("date"):
        raise CapTableError("A round needs a date.")
    rnd = Round(
        org=org, created_by=by if getattr(by, "is_authenticated", False) else None, **fields
    )
    _require_valuation(rnd)
    rnd.save()
    _added(org, by, rnd)
    _check(org)
    return rnd


@transaction.atomic
def update_round(rnd: Round, by=None, **data) -> Round:
    before = _snap(rnd)
    for k, v in _round_fields(data).items():
        setattr(rnd, k, v)
    _require_valuation(rnd)
    rnd.save()
    _record(rnd.org, by, EventKind.UPDATE, rnd, before, _snap(rnd))
    _check(rnd.org)
    return rnd


@transaction.atomic
def delete_round(rnd: Round, by=None):
    if rnd.investments.exists():
        raise CapTableError("Remove this round's investments first.")
    _deleted(rnd.org, by, rnd)


# --------------------------------------------------------------------------- #
# Investments.
# --------------------------------------------------------------------------- #
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
        created_by=by if getattr(by, "is_authenticated", False) else None,
    )
    _added(org, by, inv)
    _check(org)
    return inv


@transaction.atomic
def update_investment(inv: Investment, by=None, **data) -> Investment:
    before = _snap(inv)
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
    _record(inv.org, by, EventKind.UPDATE, inv, before, _snap(inv))
    _check(inv.org)
    return inv


@transaction.atomic
def delete_investment(inv: Investment, by=None):
    _deleted(inv.org, by, inv)


# --------------------------------------------------------------------------- #
# Stock classes.
# --------------------------------------------------------------------------- #
STOCK_CLASS_FIELDS = (
    "name",
    "class_type",
    "initial_shares_authorized",
    "par_value",
    "votes_per_share",
    "seniority",
    "board_approval_date",
)


def _class_fields(data: dict) -> dict:
    out = {}
    if "name" in data:
        name = str(data["name"] or "").strip()[:80]
        if not name:
            raise CapTableError("A stock class needs a name.")
        out["name"] = name
    if "class_type" in data:
        if data["class_type"] not in StockClassType.values:
            raise CapTableError("A stock class is common or preferred.")
        out["class_type"] = data["class_type"]
    if "initial_shares_authorized" in data:
        v = data["initial_shares_authorized"]
        if v in ("", None):
            out["initial_shares_authorized"] = None
        else:
            n = int(v)
            if n <= 0:
                raise CapTableError("Authorized shares are above zero.")
            out["initial_shares_authorized"] = n
    if "par_value" in data:
        v = data["par_value"]
        out["par_value"] = None if v in ("", None) else Decimal(str(v))
        if out["par_value"] is not None and out["par_value"] < ZERO:
            raise CapTableError("A par value is zero or more.")
    if "votes_per_share" in data:
        v = Decimal(
            str(data["votes_per_share"] if data["votes_per_share"] not in ("", None) else 0)
        )
        if v < ZERO:
            raise CapTableError("Votes per share are zero or more.")
        out["votes_per_share"] = v
    if "seniority" in data:
        n = int(data["seniority"] or 1)
        if n < 1:
            raise CapTableError("Seniority is 1 or more.")
        out["seniority"] = n
    if "board_approval_date" in data:
        out["board_approval_date"] = _date(data["board_approval_date"])
    return out


def _unique_class_name(org, name, pk=None):
    if StockClass.objects.for_org(org).filter(name=name).exclude(pk=pk).exists():
        raise CapTableError(f"There is already a class called {name}.")


@transaction.atomic
def add_stock_class(org, by=None, **data) -> StockClass:
    fields = _class_fields(data)
    if "name" not in fields:
        raise CapTableError("A stock class needs a name.")
    _unique_class_name(org, fields["name"])
    sc = StockClass.objects.create(org=org, **fields)
    _added(org, by, sc)
    return sc


@transaction.atomic
def update_stock_class(sc: StockClass, by=None, **data) -> StockClass:
    before = _snap(sc)
    fields = _class_fields(data)
    if "name" in fields:
        _unique_class_name(sc.org, fields["name"], pk=sc.pk)
    for k, v in fields.items():
        setattr(sc, k, v)
    sc.save()
    _record(sc.org, by, EventKind.UPDATE, sc, before, _snap(sc))
    return sc


@transaction.atomic
def delete_stock_class(sc: StockClass, by=None):
    if sc.holdings.exists() or sc.valuations.exists():
        raise CapTableError("Move or remove this class's stock and valuations first.")
    _deleted(sc.org, by, sc)


def resolve_stock_class(org, value, by=None) -> StockClass:
    """A class of this org from a StockClass, its id, or its name.

    A name not used yet makes a new common class of that name, so a holding typed with a
    class name (the way holdings were recorded before classes existed) still saves.
    """
    if isinstance(value, StockClass):
        if value.org_id != org.pk:
            raise CapTableError("That stock class is not in this org.")
        return value
    if isinstance(value, int) or (isinstance(value, str) and value.strip().isdigit()):
        sc = StockClass.objects.for_org(org).filter(pk=int(value)).first()
        if sc is None:
            raise CapTableError("No such stock class here.")
        return sc
    name = str(value or "").strip()[:80] or DEFAULT_CLASS_NAME
    sc = StockClass.objects.for_org(org).filter(name=name).first()
    return sc or add_stock_class(org, by=by, name=name)


def stock_classes(org):
    """Each class with how many shares of it are held of record."""
    classes = list(StockClass.objects.for_org(org))
    held = {}
    for h in StockHolding.objects.for_org(org).only("stock_class_id", "quantity"):
        held[h.stock_class_id] = held.get(h.stock_class_id, 0) + h.quantity
    for c in classes:
        c.issued = held.get(c.pk, 0)
    return classes


# --------------------------------------------------------------------------- #
# Stock holdings.
# --------------------------------------------------------------------------- #
@transaction.atomic
def add_holding(
    org,
    membership=None,
    holder=None,
    quantity=None,
    stock_class=DEFAULT_CLASS_NAME,
    date=None,
    note="",
    by=None,
):
    qty = int(quantity or 0)
    if qty <= 0:
        raise CapTableError("A share count is above zero.")
    h = StockHolding.objects.create(
        org=org,
        membership=membership,
        holder=holder,
        quantity=qty,
        stock_class=resolve_stock_class(org, stock_class, by=by),
        date=_date(date),
        note=note or "",
        created_by=by if getattr(by, "is_authenticated", False) else None,
    )
    _added(org, by, h)
    return h


@transaction.atomic
def update_holding(h: StockHolding, by=None, **data) -> StockHolding:
    before = _snap(h)
    if "quantity" in data:
        qty = int(data["quantity"] or 0)
        if qty <= 0:
            raise CapTableError("A share count is above zero.")
        h.quantity = qty
    if "stock_class" in data:
        h.stock_class = resolve_stock_class(h.org, data["stock_class"], by=by)
    if "date" in data:
        h.date = _date(data["date"])
    if "note" in data:
        h.note = data["note"] or ""
    if "membership" in data or "holder" in data:
        h.membership, h.holder = data.get("membership"), data.get("holder")
    h.save()
    _record(h.org, by, EventKind.UPDATE, h, before, _snap(h))
    return h


@transaction.atomic
def delete_holding(h: StockHolding, by=None):
    _deleted(h.org, by, h)


# --------------------------------------------------------------------------- #
# Valuations (409A or a round's price per share).
# --------------------------------------------------------------------------- #
VALUATION_FIELDS = (
    "stock_class",
    "effective_date",
    "price_per_share",
    "valuation_type",
    "provider",
    "board_approval_date",
    "report_url",
    "gross_assets",
)
_url = URLValidator(schemes=["http", "https"])


def _valuation_fields(org, data: dict) -> dict:
    out = {}
    if "stock_class" in data:
        out["stock_class"] = resolve_stock_class(org, data["stock_class"])
    if "effective_date" in data:
        out["effective_date"] = _date(data["effective_date"])
        if out["effective_date"] is None:
            raise CapTableError("A valuation needs a date.")
    if "price_per_share" in data:
        if data["price_per_share"] in ("", None):
            raise CapTableError("A valuation needs a price per share.")
        out["price_per_share"] = Decimal(str(data["price_per_share"]))
        if out["price_per_share"] < ZERO:
            raise CapTableError("A price is zero or more.")
    if "valuation_type" in data:
        if data["valuation_type"] not in ValuationType.values:
            raise CapTableError("A valuation is a 409A or a round price.")
        out["valuation_type"] = data["valuation_type"]
    if "provider" in data:
        out["provider"] = (data["provider"] or "").strip()[:200]
    if "board_approval_date" in data:
        out["board_approval_date"] = _date(data["board_approval_date"])
    if "report_url" in data:
        url = (data["report_url"] or "").strip()
        if url:
            try:
                _url(url)
            except ValidationError:
                raise CapTableError("A report link starts with https://.")
        out["report_url"] = url[:500]
    if "gross_assets" in data:
        v = data["gross_assets"]
        out["gross_assets"] = None if v in ("", None) else _nonneg(v)
    return out


def _nonneg(value) -> Decimal:
    d = Decimal(str(value))
    if d < ZERO:
        raise CapTableError("An amount is zero or more.")
    return d.quantize(CENTS)


@transaction.atomic
def add_valuation(org, by=None, **data) -> Valuation:
    fields = _valuation_fields(org, {"stock_class": DEFAULT_CLASS_NAME, **data})
    for f in ("effective_date", "price_per_share"):
        if f not in fields:
            raise CapTableError("A valuation needs a date and a price per share.")
    v = Valuation.objects.create(
        org=org, created_by=by if getattr(by, "is_authenticated", False) else None, **fields
    )
    _added(org, by, v)
    return v


@transaction.atomic
def update_valuation(v: Valuation, by=None, **data) -> Valuation:
    before = _snap(v)
    for k, val in _valuation_fields(v.org, data).items():
        setattr(v, k, val)
    v.save()
    _record(v.org, by, EventKind.UPDATE, v, before, _snap(v))
    return v


@transaction.atomic
def delete_valuation(v: Valuation, by=None):
    _deleted(v.org, by, v)


# --------------------------------------------------------------------------- #
# The company's documents: SAFE agreement, bylaws, certificate of incorporation, other.
# --------------------------------------------------------------------------- #
def documents(org):
    return list(CompanyDocument.objects.for_org(org).select_related("uploaded_by"))


@transaction.atomic
def add_document(org, kind="", title="", url="", upload=None, by=None) -> CompanyDocument:
    """A document from an uploaded file (stored in GovKit's bucket) or a pasted link."""
    kind = kind or DocumentKind.OTHER
    if kind not in DocumentKind.values:
        raise CapTableError(
            "A document is a SAFE agreement, bylaws, a certificate of incorporation or other."
        )
    url = (url or "").strip()
    if upload:
        problem = storage.check_file(upload)
        if problem:
            raise CapTableError(problem)
        if not storage.configured():
            raise CapTableError("Uploads are not set up on this server yet. Paste a link instead.")
        try:
            url = storage.store_file(upload, prefix=f"cap-documents/{org.slug}")
        except Exception:
            logger.exception("document upload failed for %s", org.slug)
            raise CapTableError("The file did not upload. Try again, or paste a link.")
    if not url:
        raise CapTableError("Choose a file or paste a link.")
    try:
        _url(url)
    except ValidationError:
        raise CapTableError("That link is not a web address.")
    title = (title or "").strip()[:200] or (
        (upload.name if upload else "") or DocumentKind(kind).label
    )[:200]
    doc = CompanyDocument.objects.create(
        org=org,
        kind=kind,
        title=title,
        url=url,
        uploaded_by=by if getattr(by, "is_authenticated", False) else None,
    )
    _added(org, by, doc)
    return doc


@transaction.atomic
def delete_document(doc: CompanyDocument, by=None):
    _deleted(doc.org, by, doc)


# --------------------------------------------------------------------------- #
# Delaware franchise tax.
#
# Delaware Division of Corporations, fetched 2026-10-21:
#   https://corp.delaware.gov/frtaxcalc/  (how to calculate)
#   https://corp.delaware.gov/paytaxes/   (minimums, maximum, due date)
#
# Authorized Shares Method: "5,000 shares or less (minimum tax) $175.00. 5,001 - 10,000
# shares - $250.00, each additional 10,000 shares or portion thereof add $85.00 maximum
# annual tax is $200,000.00".
#
# Assumed Par Value Capital Method: "Divide your total gross assets by your total issued
# shares carrying to 6 decimal places" (the assumed par); multiply it by the authorized
# shares whose par value is less than it; multiply authorized shares whose par value is
# greater by their own par; the sum is the assumed par value capital. Tax is $400.00 per
# million or portion of a million ("rounded up to the next million if it is over
# $1,000,000"); "The minimum tax for the Assumed Par Value Capital Method of calculation is
# $400.00". Gross assets are "those 'total assets' reported on the U.S. Form 1120,
# Schedule L". Maximum $200,000 for both methods; $250,000 for a Large Corporate Filer
# (paytaxes page), which this does not model. Due March 1 for the prior year.
# --------------------------------------------------------------------------- #
DE_ASM_MIN = Decimal("175.00")  # 5,000 authorized shares or fewer
DE_ASM_MIN_SHARES = 5_000
DE_ASM_TIER2 = Decimal("250.00")  # 5,001 - 10,000
DE_ASM_TIER2_SHARES = 10_000
DE_ASM_STEP = Decimal("85.00")  # each further 10,000 or part
DE_ASM_STEP_SHARES = 10_000
DE_APVC_RATE = Decimal("400.00")  # per $1,000,000 or part
DE_APVC_UNIT = Decimal("1000000")
DE_APVC_MIN = Decimal("400.00")
DE_MAX = Decimal("200000.00")
DE_ASSUMED_PAR_Q = Decimal("0.000001")


@dataclass
class FranchiseTax:
    year: int
    due_date: object
    gross_assets: Optional[Decimal]
    issued_shares: Optional[int]
    issued_shares_stated: bool  # False = the stock of record's total
    authorized_shares: int
    classes_missing_authorized: List[str]
    authorized_shares_method: Optional[Decimal]
    assumed_par: Optional[Decimal]
    assumed_par_value_capital: Optional[Decimal]
    assumed_par_value_capital_method: Optional[Decimal]
    lesser: Optional[Decimal]
    lesser_method: str  # "authorized_shares" / "assumed_par_value_capital" / ""


def de_authorized_shares_tax(authorized: int) -> Decimal:
    if authorized <= DE_ASM_MIN_SHARES:
        return DE_ASM_MIN
    if authorized <= DE_ASM_TIER2_SHARES:
        return DE_ASM_TIER2
    steps = math.ceil((authorized - DE_ASM_TIER2_SHARES) / DE_ASM_STEP_SHARES)
    return min(DE_ASM_TIER2 + DE_ASM_STEP * steps, DE_MAX)


def de_assumed_par_value_capital(gross_assets: Decimal, issued: int, classes) -> tuple:
    """(assumed par, assumed par value capital) over [(authorized shares, par or None)]."""
    assumed = (Decimal(gross_assets) / Decimal(issued)).quantize(DE_ASSUMED_PAR_Q, ROUND_HALF_UP)
    capital = ZERO
    for shares, par in classes:
        if par is not None and par >= assumed:
            capital += Decimal(shares) * par
        else:
            capital += Decimal(shares) * assumed
    return assumed, capital


def de_assumed_par_value_capital_tax(capital: Decimal) -> Decimal:
    if capital > DE_APVC_UNIT:
        millions = (capital / DE_APVC_UNIT).to_integral_value(ROUND_CEILING)
        tax = millions * DE_APVC_RATE
    else:
        tax = capital / DE_APVC_UNIT * DE_APVC_RATE
    return min(max(tax, DE_APVC_MIN), DE_MAX).quantize(CENTS)


def franchise_tax(org, year: int) -> FranchiseTax:
    """Both Delaware methods for one tax year, and the lesser. Reads; never writes."""
    from datetime import date as _d

    inputs = FranchiseTaxYear.objects.for_org(org).filter(year=year).first()
    classes = stock_classes(org)
    authorized = sum((c.initial_shares_authorized or 0) for c in classes)
    missing = [c.name for c in classes if c.initial_shares_authorized is None]
    stated = inputs is not None and inputs.issued_shares is not None
    issued = inputs.issued_shares if stated else sum(c.issued for c in classes)
    gross = inputs.gross_assets if inputs else None

    asm = de_authorized_shares_tax(authorized) if authorized else None
    assumed = capital = apvc = None
    if authorized and gross is not None and issued:
        assumed, capital = de_assumed_par_value_capital(
            gross,
            issued,
            [
                (c.initial_shares_authorized, c.par_value)
                for c in classes
                if c.initial_shares_authorized
            ],
        )
        apvc = de_assumed_par_value_capital_tax(capital)
    options = [
        (t, m)
        for t, m in ((asm, "authorized_shares"), (apvc, "assumed_par_value_capital"))
        if t is not None
    ]
    lesser, lesser_method = min(options, key=lambda o: o[0]) if options else (None, "")
    return FranchiseTax(
        year=year,
        due_date=_d(year + 1, 3, 1),
        gross_assets=gross,
        issued_shares=issued or None,
        issued_shares_stated=stated,
        authorized_shares=authorized,
        classes_missing_authorized=missing,
        authorized_shares_method=asm,
        assumed_par=assumed,
        assumed_par_value_capital=capital.quantize(CENTS) if capital is not None else None,
        assumed_par_value_capital_method=apvc,
        lesser=lesser,
        lesser_method=lesser_method,
    )


@transaction.atomic
def set_franchise_tax_inputs(org, year, by=None, **data) -> FranchiseTaxYear:
    try:
        year = int(year)
    except (TypeError, ValueError):
        raise CapTableError("A tax year is a year, like 2026.")
    if year < 1900 or year > 2200:
        raise CapTableError("A tax year is a year, like 2026.")
    row = FranchiseTaxYear.objects.for_org(org).filter(year=year).first()
    created = row is None
    if created:
        row = FranchiseTaxYear(org=org, year=year)
    before = None if created else _snap(row)
    if "gross_assets" in data:
        v = data["gross_assets"]
        row.gross_assets = None if v in ("", None) else _nonneg(v)
    if "issued_shares" in data:
        v = data["issued_shares"]
        if v in ("", None):
            row.issued_shares = None
        else:
            n = int(v)
            if n <= 0:
                raise CapTableError("Issued shares are above zero.")
            row.issued_shares = n
    row.save()
    if created:
        _added(org, by, row)
    else:
        _record(org, by, EventKind.UPDATE, row, before, _snap(row))
    return row
