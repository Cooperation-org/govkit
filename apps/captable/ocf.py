"""
Open Cap Format export: the cap table as an OCF package, so the ledger is portable.

Spec: github.com/Open-Cap-Table-Coalition/Open-Cap-Format-OCF (schema/files/*). A
package is a manifest plus one file per object kind; the recommended transport is the
files zipped as ``<name>.ocf.zip``. :func:`build_package` returns the files as a dict
keyed by their package filename, each one exactly the OCF file object, so the JSON
endpoint and the zip carry the same bytes.

How the cap table maps onto OCF:

* Every holder is a ``STAKEHOLDER`` (members INDIVIDUAL; outside holders by their
  ``stakeholder_type``).
* A founders-pool line is a ``TX_CONVERTIBLE_ISSUANCE`` of type CONVERTIBLE_SECURITY
  whose conversion right is FIXED_PERCENT_OF_CAPITALIZATION_CONVERSION at the line's
  percent of the company — the pool is a percent of the company, earned through
  reviewed work, with no money in (investment_amount 0). The pie keeps diluting inside
  the pool, so each export restates these as of its ``as_of`` date.
* A SAFE investment is a ``TX_CONVERTIBLE_ISSUANCE`` of type SAFE with a SAFE_CONVERSION
  mechanism from the round's terms: conversion_timing, conversion_mfn, conversion_discount,
  and conversion_valuation_cap (the stated cap, else the round's post-money for a
  post-money SAFE or pre-money for a pre-money one). A pro rata right is a comment: OCF
  ``pro_rata`` is an amount, which the round does not state.
* A note is a ``TX_CONVERTIBLE_ISSUANCE`` of type NOTE with CONVERTIBLE_NOTE_CONVERSION:
  its interest rate accruing from the investment date, its discount and cap. The day
  count, payout, accrual period and compounding are OCF-required and not recorded; the
  standard defaults are filled in and said so. The maturity date is a comment.
* A priced-round investment with a price per share is a ``TX_STOCK_ISSUANCE`` of that
  round's preferred class (quantity = amount ÷ price). Without a price it cannot state a
  share count, so it is listed in the manifest comments instead of guessed.
* A stock holding of record is a ``TX_STOCK_ISSUANCE`` of its class.
* Each round with investments is a ``FINANCING``; each valued round is noted in the
  manifest comments.
* Each 409A ``Valuation`` is an OCF ``VALUATION``; a round-price valuation is a manifest
  comment (OCF's ValuationType has only 409A).
* Every recorded ``StockClass`` is a ``STOCK_CLASS`` with its own authorized shares, par,
  votes and seniority.
* The unsold investor reserve is a manifest comment: OCF has no object for an
  unissued percent of the company.

A class with no authorized shares recorded says "NOT APPLICABLE" and a comment. A priced
round with a price and no recorded class of its name gets a generated preferred class
with the standard defaults, said so in its comments: votes_per_share 1, initial shares
authorized "NOT APPLICABLE", each priced round's preferred one above the last.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from django.utils import timezone

from apps.orgs.models import ExternalHolder, Membership, StakeholderType

from .models import (
    ConversionTiming,
    EntityType,
    Instrument,
    Investment,
    Round,
    Scope,
    StockClass,
    StockHolding,
    Valuation,
    ValuationType,
)
from .services import (
    KIND_FOUNDER,
    KIND_POOL_UNEARNED,
    KIND_SPONSOR,
    compute_cap_table,
    get_settings,
    round_valuations,
)

OCF_VERSION = "1.2.1-alpha+main"

FILES = {
    "stakeholders": ("Stakeholders.ocf.json", "OCF_STAKEHOLDERS_FILE"),
    "stock_classes": ("StockClasses.ocf.json", "OCF_STOCK_CLASSES_FILE"),
    "transactions": ("Transactions.ocf.json", "OCF_TRANSACTIONS_FILE"),
    "financings": ("Financings.ocf.json", "OCF_FINANCINGS_FILE"),
    "stock_plans": ("StockPlans.ocf.json", "OCF_STOCK_PLANS_FILE"),
    "stock_legend_templates": ("StockLegends.ocf.json", "OCF_STOCK_LEGEND_TEMPLATES_FILE"),
    "vesting_terms": ("VestingTerms.ocf.json", "OCF_VESTING_TERMS_FILE"),
    "valuations": ("Valuations.ocf.json", "OCF_VALUATIONS_FILE"),
}
DEFAULTS_NOTE = (
    "votes_per_share, initial_shares_authorized and seniority are OCF-required and not "
    "recorded in GovKit; standard defaults filled in."
)
AUTHORIZED_NOTE = "Authorized shares not recorded."
NOTE_DEFAULTS_NOTE = (
    "Day count, interest payout, accrual period and compounding are OCF-required and not "
    "recorded in GovKit; ACTUAL_365, DEFERRED, ANNUAL and SIMPLE filled in."
)


def _num(value, places=10) -> str:
    """OCF Numeric: a plain decimal string, at most 10 places."""
    q = Decimal(value).quantize(Decimal(1).scaleb(-places))
    text = format(q.normalize(), "f")
    return text if text not in ("-0",) else "0"


def _money(amount, currency):
    return {"amount": _num(amount), "currency": currency}


def _member_id(pk):
    return f"member-{pk}"


def _holder_id(pk):
    return f"holder-{pk}"


def _stakeholder_key(membership_id, holder_id):
    return _member_id(membership_id) if membership_id else _holder_id(holder_id)


def build_package(org) -> dict:
    table = compute_cap_table(org)
    cur = table.currency
    today = timezone.localdate()
    as_of = today.isoformat()

    # --- stakeholders: everyone who holds a line, and only them -------------
    stakeholders = {}

    def add_member(mid, relationship):
        key = _member_id(mid)
        if key in stakeholders:
            rels = stakeholders[key]["current_relationships"]
            if relationship not in rels:
                rels.append(relationship)
            return key
        m = Membership.objects.select_related("user").get(pk=mid)
        stakeholders[key] = {
            "object_type": "STAKEHOLDER",
            "id": key,
            "name": {"legal_name": m.user.get_full_name() or m.user.get_short_name()},
            "stakeholder_type": StakeholderType.INDIVIDUAL,
            "issuer_assigned_id": key,
            "current_relationships": [relationship],
        }
        return key

    def add_holder(holder: ExternalHolder, relationship):
        key = _holder_id(holder.pk)
        if key in stakeholders:
            rels = stakeholders[key]["current_relationships"]
            if relationship not in rels:
                rels.append(relationship)
            return key
        stakeholders[key] = {
            "object_type": "STAKEHOLDER",
            "id": key,
            "name": {"legal_name": holder.display_name},
            "stakeholder_type": holder.stakeholder_type,
            "issuer_assigned_id": holder.slug,
            "current_relationships": [relationship],
        }
        return key

    transactions = []
    comments = [
        f"Founders pool {table.founders_pool_pct}% of the company; investor tranche "
        f"{table.investor_pool_pct}%.",
    ]

    # --- founders pool ------------------------------------------------------
    for i, ln in enumerate(table.lines):
        if ln.kind not in (KIND_FOUNDER, KIND_SPONSOR):
            continue
        if ln.kind == KIND_FOUNDER:
            sh = add_member(ln.membership_id, "FOUNDER")
        else:
            holder = ExternalHolder.objects.get(slug=ln.holder_slug)
            sh = add_holder(holder, "OTHER")
        sec = f"pool-{sh}"
        transactions.append(
            {
                "object_type": "TX_CONVERTIBLE_ISSUANCE",
                "id": f"tx-{sec}",
                "security_id": sec,
                "date": as_of,
                "stakeholder_id": sh,
                "custom_id": f"POOL-{i + 1}",
                "security_law_exemptions": [],
                "convertible_type": "CONVERTIBLE_SECURITY",
                "investment_amount": _money(0, cur),
                "consideration_text": (
                    f"Reviewed work: {ln.pie_total} {table.unit_name}, "
                    f"{ln.pie_share_pct}% of the founders pool."
                ),
                "conversion_triggers": [
                    {
                        "trigger_id": f"{sec}.TRIG.1",
                        "type": "UNSPECIFIED",
                        "conversion_right": {
                            "type": "CONVERTIBLE_CONVERSION_RIGHT",
                            "conversion_mechanism": {
                                "type": "FIXED_PERCENT_OF_CAPITALIZATION_CONVERSION",
                                "converts_to_percent": _num(ln.pct / 100),
                            },
                        },
                    }
                ],
                "seniority": 2,
                "comments": [
                    "Founders pool share as of the export date; the pool dilutes as work is issued."
                ],
            }
        )
    if any(ln.kind == KIND_POOL_UNEARNED for ln in table.lines):
        comments.append("The founders pool has no issued work yet.")

    # --- stock classes: every recorded class, plus one per priced round ------
    stock_classes = {}
    class_key_by_pk = {}
    for sc in StockClass.objects.for_org(org):
        key = f"class-{sc.pk}"
        obj = {
            "object_type": "STOCK_CLASS",
            "id": key,
            "name": sc.name,
            "class_type": sc.class_type,
            "default_id_prefix": "PS-" if sc.class_type == "PREFERRED" else "CS-",
            "initial_shares_authorized": (
                str(sc.initial_shares_authorized)
                if sc.initial_shares_authorized is not None
                else "NOT APPLICABLE"
            ),
            "votes_per_share": _num(sc.votes_per_share),
            "seniority": _num(sc.seniority),
        }
        if sc.par_value is not None:
            obj["par_value"] = _money(sc.par_value, cur)
        if sc.board_approval_date:
            obj["board_approval_date"] = sc.board_approval_date.isoformat()
        if sc.initial_shares_authorized is None:
            obj["comments"] = [AUTHORIZED_NOTE]
        stock_classes[key] = obj
        class_key_by_pk[sc.pk] = key

    def stock_class(name, class_type, seniority, price=None):
        for k, c in stock_classes.items():
            if c["name"] == name:
                return k
        key = f"class-{class_type.lower()}-{len(stock_classes) + 1}"
        obj = {
            "object_type": "STOCK_CLASS",
            "id": key,
            "name": name,
            "class_type": class_type,
            "default_id_prefix": "PS-" if class_type == "PREFERRED" else "CS-",
            "initial_shares_authorized": "NOT APPLICABLE",
            "votes_per_share": "1",
            "seniority": str(seniority),
            "comments": [DEFAULTS_NOTE],
        }
        if price is not None:
            obj["price_per_share"] = _money(price, cur)
        stock_classes[key] = obj
        return key

    holdings = StockHolding.objects.for_org(org).select_related(
        "membership__user", "holder", "stock_class"
    )
    for h in holdings:
        cls = class_key_by_pk[h.stock_class_id]
        prefix = stock_classes[cls]["default_id_prefix"]
        sh = (
            add_member(h.membership_id, "FOUNDER")
            if h.membership_id
            else add_holder(h.holder, "OTHER")
        )
        sec = f"stock-{h.pk}"
        tx = {
            "object_type": "TX_STOCK_ISSUANCE",
            "id": f"tx-{sec}",
            "security_id": sec,
            "date": (h.date or today).isoformat(),
            "stakeholder_id": sh,
            "custom_id": f"{prefix}{h.pk}",
            "security_law_exemptions": [],
            "stock_class_id": cls,
            "share_price": _money(0, cur),
            "quantity": str(h.quantity),
            "stock_legend_ids": [],
        }
        if h.note:
            tx["comments"] = [h.note]
        transactions.append(tx)

    # --- investments --------------------------------------------------------
    financings = []
    preferred_seniority = max((int(c["seniority"]) for c in stock_classes.values()), default=1)
    rounds = list(Round.objects.for_org(org))
    invs = list(
        Investment.objects.for_org(org).select_related("round", "membership__user", "holder")
    )
    for rnd in rounds:
        mine = [i for i in invs if i.round_id == rnd.pk]
        raised = sum((i.amount for i in mine), Decimal("0"))
        pre, post = round_valuations(rnd, raised)
        comments.append(
            f"{rnd.name} ({rnd.get_instrument_display()}, {rnd.date.isoformat()}): "
            f"pre-money {pre} {cur}, post-money {post} {cur}, raised {raised} {cur}."
        )
        cls = None
        if rnd.instrument == Instrument.PRICED and rnd.price_per_share:
            preferred_seniority += 1
            cls = stock_class(
                f"{rnd.name} Preferred", "PREFERRED", preferred_seniority, rnd.price_per_share
            )
        ids = []
        for inv in mine:
            sh = (
                add_member(inv.membership_id, "INVESTOR")
                if inv.membership_id
                else add_holder(inv.holder, "INVESTOR")
            )
            sec = f"inv-{inv.pk}"
            base = {
                "id": f"tx-{sec}",
                "security_id": sec,
                "date": (inv.date or rnd.date).isoformat(),
                "stakeholder_id": sh,
                "security_law_exemptions": [],
            }
            if inv.note:
                base["comments"] = [inv.note]
            if rnd.instrument in (Instrument.SAFE, Instrument.NOTE):
                tx = {
                    "object_type": "TX_CONVERTIBLE_ISSUANCE",
                    **base,
                    "custom_id": f"{'SAFE' if rnd.instrument == Instrument.SAFE else 'NOTE'}-{inv.pk}",
                    "convertible_type": "SAFE" if rnd.instrument == Instrument.SAFE else "NOTE",
                    "investment_amount": _money(inv.amount, cur),
                    "conversion_triggers": [
                        {
                            "trigger_id": f"{sec}.TRIG.1",
                            "type": "UNSPECIFIED",
                            "conversion_right": {
                                "type": "CONVERTIBLE_CONVERSION_RIGHT",
                                "conversion_mechanism": _convertible_mechanism(
                                    rnd, inv, pre, post, cur
                                ),
                            },
                        }
                    ],
                    "seniority": 1,
                }
                extra = _convertible_comments(rnd)
                if extra:
                    tx["comments"] = tx.get("comments", []) + extra
            elif cls:
                tx = {
                    "object_type": "TX_STOCK_ISSUANCE",
                    **base,
                    "custom_id": f"PS-{inv.pk}",
                    "stock_class_id": cls,
                    "share_price": _money(rnd.price_per_share, cur),
                    "quantity": _num(inv.amount / rnd.price_per_share),
                    "cost_basis": _money(inv.amount, cur),
                    "stock_legend_ids": [],
                }
            else:
                comments.append(
                    f"{inv.holder_label} invested {inv.amount} {cur} in {rnd.name}; "
                    "no price per share recorded, so no share count is stated."
                )
                continue
            transactions.append(tx)
            ids.append(tx["id"])
        if ids:
            financings.append(
                {
                    "object_type": "FINANCING",
                    "id": f"round-{rnd.pk}",
                    "name": rnd.name,
                    "issuance_ids": ids,
                    "date": rnd.date.isoformat(),
                }
            )

    if table.unsold_pct > 0:
        comments.append(
            f"Not yet sold: {table.unsold_pct.quantize(Decimal('0.01'))}% of the company."
        )
    if table.latest_post_money is not None:
        comments.append(
            f"Latest post-money valuation: {table.latest_post_money} {cur} ({table.latest_round_name})."
        )

    # --- valuations ----------------------------------------------------------
    valuations = []
    for v in (
        Valuation.objects.for_org(org)
        .select_related("stock_class")
        .order_by("effective_date", "id")
    ):
        if v.valuation_type != ValuationType.FOUR_09A:
            comments.append(
                f"{v.get_valuation_type_display()} {v.effective_date.isoformat()}: "
                f"{_num(v.price_per_share)} {cur} per share of {v.stock_class.name}."
            )
            continue
        obj = {
            "object_type": "VALUATION",
            "id": f"valuation-{v.pk}",
            "stock_class_id": class_key_by_pk[v.stock_class_id],
            "price_per_share": _money(v.price_per_share, cur),
            "effective_date": v.effective_date.isoformat(),
            "valuation_type": "409A",
        }
        if v.provider:
            obj["provider"] = v.provider
        if v.board_approval_date:
            obj["board_approval_date"] = v.board_approval_date.isoformat()
        notes = []
        if v.report_url:
            notes.append(f"Report: {v.report_url}")
        if v.gross_assets is not None:
            notes.append(f"Gross assets on that date: {_num(v.gross_assets)} {cur}.")
        if notes:
            obj["comments"] = notes
        valuations.append(obj)

    cfg = get_settings(org)
    if cfg.entity_type:
        comments.append(f"Entity type: {EntityType(cfg.entity_type).label}.")
    if cfg.incorporated:
        comments.append("Incorporated.")
    if cfg.scope == Scope.POOL_ONLY:
        comments.append(
            f"This record holds the founders pool only; {table.investor_pool_pct}% of the "
            "company is held outside it."
        )

    items = {
        "stakeholders": list(stakeholders.values()),
        "stock_classes": list(stock_classes.values()),
        "transactions": transactions,
        "financings": financings,
        "stock_plans": [],
        "stock_legend_templates": [],
        "vesting_terms": [],
        "valuations": valuations,
    }
    files = {}
    manifest_refs = {}
    for kind, (filename, file_type) in FILES.items():
        body = {"file_type": file_type, "items": items[kind]}
        files[filename] = body
        manifest_refs[f"{kind}_files"] = [
            {"filepath": f"./{filename}", "md5": hashlib.md5(_dump(body)).hexdigest()}
        ]

    issuer = {
        "object_type": "ISSUER",
        "id": f"issuer-{org.slug}",
        "legal_name": cfg.legal_name or org.display_name,
        "formation_date": (
            cfg.formation_date or (org.genesis_started_at or org.created_at).date()
        ).isoformat(),
        "country_of_formation": cfg.country_of_formation or "US",
    }
    if cfg.country_subdivision_of_formation:
        issuer["country_subdivision_of_formation"] = cfg.country_subdivision_of_formation
    if cfg.legal_name and org.display_name != cfg.legal_name:
        issuer["dba"] = org.display_name
    missing = [
        f
        for f, v in (
            ("legal_name", cfg.legal_name),
            ("formation_date", cfg.formation_date),
            ("country_of_formation", cfg.country_of_formation),
        )
        if not v
    ]
    if missing:
        # OCF requires all three. Until the entity is recorded, say which were filled in.
        issuer["comments"] = [
            "Not yet recorded, filled from the team's name, start date and US: "
            + ", ".join(missing)
        ]
    manifest = {
        "ocf_version": OCF_VERSION,
        "file_type": "OCF_MANIFEST_FILE",
        "issuer": issuer,
        "as_of": as_of,
        "generated_at": timezone.now().isoformat(),
        "comments": comments,
        **manifest_refs,
    }
    return {"Manifest.ocf.json": manifest, **files}


def _convertible_mechanism(rnd, inv, pre, post, cur) -> dict:
    """SAFE_CONVERSION or CONVERTIBLE_NOTE_CONVERSION from the round's terms."""
    # A note's cap is a pre-money number; a SAFE says which it is.
    pre_money = (
        rnd.instrument == Instrument.NOTE or rnd.conversion_timing == ConversionTiming.PRE_MONEY
    )
    cap = rnd.conversion_valuation_cap or (pre if pre_money else post)
    mech = {"conversion_mfn": rnd.conversion_mfn}
    if rnd.conversion_discount is not None:
        mech["conversion_discount"] = _num(rnd.conversion_discount)
    if cap:
        mech["conversion_valuation_cap"] = _money(cap, cur)
    if rnd.instrument == Instrument.SAFE:
        return {
            "type": "SAFE_CONVERSION",
            **mech,
            "conversion_timing": rnd.conversion_timing,
        }
    return {
        "type": "CONVERTIBLE_NOTE_CONVERSION",
        **mech,
        "interest_rates": (
            [
                {
                    "rate": _num(rnd.interest_rate),
                    "accrual_start_date": (inv.date or rnd.date).isoformat(),
                }
            ]
            if rnd.interest_rate is not None
            else []
        ),
        "day_count_convention": "ACTUAL_365",
        "interest_payout": "DEFERRED",
        "interest_accrual_period": "ANNUAL",
        "compounding_type": "SIMPLE",
    }


def _convertible_comments(rnd) -> list:
    out = []
    if rnd.instrument == Instrument.NOTE:
        out.append(NOTE_DEFAULTS_NOTE)
        if rnd.maturity_date:
            out.append(f"Matures {rnd.maturity_date.isoformat()}.")
    if rnd.pro_rata:
        out.append("Pro rata right in the next round.")
    return out


def _dump(obj) -> bytes:
    return json.dumps(obj, indent=2, ensure_ascii=False).encode("utf-8")


def package_bytes(files: dict) -> dict:
    """Each file serialised exactly as it goes into the zip (and as its md5 was taken)."""
    return {name: _dump(body) for name, body in files.items()}
