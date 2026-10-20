"""
The cap table: the pie runs inside a founders pool, investors buy from the rest.

What is pinned down here:

* The lines always sum to 100% of the company, with or without a pie, rounds, a sponsor.
* A founder's percent of the company is their pie share times the pool percent, read
  from compute_pie and never changing it.
* An investment buys amount ÷ post-money; what the tranche has not sold is its own line.
* A write that would sell investors more than their tranche is refused and leaves
  nothing behind.
* Members read; only admins write; non-members get nothing.
* The OCF export is a coherent package (manifest md5s match the files it names).
"""

import hashlib
import io
import json
import zipfile
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.captable import services
from apps.captable.models import Investment, Round
from apps.captable.ocf import package_bytes
from apps.orgs.models import (
    ExternalHolder,
    MembershipRole,
    OpeningBalance,
    OrgStake,
    StakeholderType,
)
from apps.pie.services import compute_pie

EMBED = {"HTTP_X_GOVKIT_EMBED": "1"}


@pytest.fixture
def venture(org_factory, user_factory, membership_factory):
    org = org_factory(slug="venture", display_name="Venture")
    admin_user = user_factory(email="admin@example.com")
    member_user = user_factory(email="member@example.com")
    admin = membership_factory(org=org, user=admin_user, role=MembershipRole.ADMIN)
    member = membership_factory(org=org, user=member_user)
    return org, admin, member


def _seed_pie(org, admin, member):
    OpeningBalance.objects.create(org=org, membership=admin, value=Decimal("600"))
    OpeningBalance.objects.create(org=org, membership=member, value=Decimal("400"))


def _total(table):
    return sum((ln.pct for ln in table.lines), Decimal("0"))


def test_empty_org_is_pool_plus_unsold(venture):
    org, _, _ = venture
    t = services.compute_cap_table(org)
    kinds = [ln.kind for ln in t.lines]
    assert kinds == [services.KIND_POOL_UNEARNED, services.KIND_UNSOLD]
    assert t.lines[0].pct == Decimal("55.00")
    assert t.lines[1].pct == Decimal("45.00")
    assert t.total_pct == Decimal("100.00")
    assert t.total_value is None


def test_founders_are_pie_share_times_pool(venture):
    org, admin, member = venture
    _seed_pie(org, admin, member)
    before = [(s.member_label, s.share) for s in compute_pie(org).slices]

    t = services.compute_cap_table(org)
    founders = {ln.membership_id: ln for ln in t.lines if ln.kind == services.KIND_FOUNDER}
    assert founders[admin.pk].pct == Decimal("33")  # 60% of 55
    assert founders[member.pk].pct == Decimal("22")  # 40% of 55
    assert founders[admin.pk].pie_share_pct == Decimal("60.00")
    assert t.total_pct == Decimal("100.00")
    # The cap table reads the pie; it never changes it.
    assert [(s.member_label, s.share) for s in compute_pie(org).slices] == before


def test_sponsor_stake_sits_inside_the_pool(venture):
    org, admin, member = venture
    _seed_pie(org, admin, member)
    sponsor = ExternalHolder.objects.create(slug="sponsor-co", display_name="Sponsor Co")
    OrgStake.objects.create(org=org, holder=sponsor, target_pct=Decimal("3"))

    t = services.compute_cap_table(org)
    line = next(ln for ln in t.lines if ln.kind == services.KIND_SPONSOR)
    assert line.pie_share_pct == Decimal("3.00")
    assert line.pct.quantize(Decimal("0.01")) == Decimal("1.65")  # 3% of a 55% pool
    assert _total(t).quantize(Decimal("0.0001")) == Decimal("100.0000")
    assert t.total_pct == Decimal("100.00")


def test_safe_investment_is_amount_over_post_money(venture):
    org, admin, member = venture
    _seed_pie(org, admin, member)
    rnd = services.add_round(
        org, name="Seed", date="2026-10-01", instrument="safe", post_money_valuation="1000000"
    )
    investor = ExternalHolder.objects.create(
        slug="angel", display_name="Angel", stakeholder_type=StakeholderType.INDIVIDUAL
    )
    services.add_investment(org, rnd, holder=investor, amount="100000")

    t = services.compute_cap_table(org)
    inv = next(ln for ln in t.lines if ln.kind == services.KIND_INVESTOR)
    unsold = next(ln for ln in t.lines if ln.kind == services.KIND_UNSOLD)
    assert inv.pct == Decimal("10")
    assert inv.value == Decimal("100000.00")
    assert unsold.pct == Decimal("35")
    assert t.latest_post_money == Decimal("1000000")
    assert t.total_pct == Decimal("100.00")
    assert t.total_value == Decimal("1000000.00")
    admin_line = next(ln for ln in t.lines if ln.membership_id == admin.pk)
    assert admin_line.value == Decimal("330000.00")


def test_priced_round_post_is_pre_plus_raised(venture):
    org, admin, _ = venture
    rnd = services.add_round(
        org,
        name="A",
        date="2026-10-02",
        instrument="priced",
        pre_money_valuation="900000",
        price_per_share="1",
    )
    services.add_investment(org, rnd, membership=admin, amount="100000")
    t = services.compute_cap_table(org)
    assert t.rounds[0].post_money == Decimal("1000000")
    assert t.rounds[0].pre_money == Decimal("900000")
    assert t.rounds[0].raised == Decimal("100000")
    assert next(ln for ln in t.lines if ln.kind == services.KIND_INVESTOR).pct == Decimal("10")


def test_valuation_history_uses_latest_round(venture):
    org, admin, member = venture
    _seed_pie(org, admin, member)
    services.add_round(org, name="Pre-seed", date="2026-01-01", post_money_valuation="500000")
    services.add_round(org, name="Seed", date="2026-06-01", post_money_valuation="2000000")
    t = services.compute_cap_table(org)
    assert [r.name for r in t.rounds] == ["Pre-seed", "Seed"]
    assert t.latest_round_name == "Seed"
    assert t.total_value == Decimal("2000000.00")


def test_overselling_the_tranche_is_refused_and_rolled_back(venture):
    org, admin, _ = venture
    rnd = services.add_round(org, name="Seed", date="2026-10-01", post_money_valuation="1000000")
    with pytest.raises(services.CapTableError):
        services.add_investment(org, rnd, membership=admin, amount="500000")  # 50% > 45%
    assert not Investment.objects.exists()

    services.add_investment(org, rnd, membership=admin, amount="400000")  # 40%
    with pytest.raises(services.CapTableError):
        services.set_settings(org, founders_pool_pct="65")  # tranche 35% < 40% sold
    assert services.get_settings(org).founders_pool_pct == Decimal("55.00")
    with pytest.raises(services.CapTableError):
        services.update_round(rnd, post_money_valuation="800000")  # 50% now
    rnd.refresh_from_db()
    assert rnd.post_money_valuation == Decimal("1000000")


def test_round_needs_a_valuation_and_empty_rounds_only_delete(venture):
    org, admin, _ = venture
    with pytest.raises(services.CapTableError):
        services.add_round(org, name="X", date="2026-10-01")
    rnd = services.add_round(org, name="Seed", date="2026-10-01", post_money_valuation="1000000")
    services.add_investment(org, rnd, membership=admin, amount="1000")
    with pytest.raises(services.CapTableError):
        services.delete_round(rnd)
    Investment.objects.all().delete()
    services.delete_round(rnd)
    assert not Round.objects.exists()


def test_stock_of_record_is_listed_not_added(venture):
    org, admin, member = venture
    services.add_holding(org, membership=admin, quantity=7_000_000)
    services.add_holding(org, membership=member, quantity=3_000_000)
    t = services.compute_cap_table(org)
    assert t.shares_of_record == 10_000_000
    assert [h.pct_of_shares for h in t.holdings] == [Decimal("70.00"), Decimal("30.00")]
    assert t.total_pct == Decimal("100.00")


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
def _api(org, name="captable-summary", **kw):
    return reverse(name, kwargs={"org_slug": org.slug, **kw})


def test_api_members_read_admins_write_outsiders_refused(client, venture, user_factory):
    org, admin, member = venture
    client.force_login(member.user)
    assert client.get(_api(org)).status_code == 200
    r = client.post(
        _api(org, "captable-rounds"),
        {"name": "Seed", "date": "2026-10-01", "post_money_valuation": "1000000"},
        **EMBED,
    )
    assert r.status_code == 403

    outsider = user_factory(email="outsider@example.com")
    client.force_login(outsider)
    assert client.get(_api(org)).status_code == 403

    client.logout()
    assert client.get(_api(org)).status_code == 401


def test_api_admin_builds_a_table(client, venture):
    org, admin, member = venture
    _seed_pie(org, admin, member)
    client.force_login(admin.user)

    r = client.patch(
        _api(org, "captable-settings"),
        {"founders_pool_pct": "60"},
        content_type="application/json",
        **EMBED,
    )
    assert r.status_code == 200 and r.json()["founders_pool_pct"] == "60.00"

    r = client.post(
        _api(org, "captable-rounds"),
        {
            "name": "Seed",
            "date": "2026-10-01",
            "instrument": "safe",
            "post_money_valuation": "2000000",
        },
        content_type="application/json",
        **EMBED,
    )
    assert r.status_code == 201
    rid = r.json()["rounds"][0]["round_id"]

    r = client.post(
        _api(org, "captable-investments"),
        {
            "round": rid,
            "holder_name": "Outside Angel",
            "holder_type": "INDIVIDUAL",
            "amount": "200000",
        },
        content_type="application/json",
        **EMBED,
    )
    assert r.status_code == 201
    body = r.json()
    inv = next(ln for ln in body["lines"] if ln["kind"] == "investor")
    assert inv["label"] == "Outside Angel" and inv["pct"] == "10.00" and inv["value"] == "200000.00"
    assert next(ln for ln in body["lines"] if ln["kind"] == "unsold")["pct"] == "30.00"
    assert body["total_pct"] == "100.00"
    assert ExternalHolder.objects.get(display_name="Outside Angel").stakeholder_type == "INDIVIDUAL"

    # Overselling answers 400 and saves nothing.
    r = client.post(
        _api(org, "captable-investments"),
        {"round": rid, "holder": f"m:{member.pk}", "amount": "900000"},
        content_type="application/json",
        **EMBED,
    )
    assert r.status_code == 400
    assert Investment.objects.count() == 1

    iid = inv["investment_id"]
    r = client.patch(
        _api(org, "captable-investment", pk=iid),
        {"amount": "100000"},
        content_type="application/json",
        **EMBED,
    )
    assert r.status_code == 200
    assert next(ln for ln in r.json()["lines"] if ln["kind"] == "investor")["pct"] == "5.00"
    assert client.delete(_api(org, "captable-investment", pk=iid), **EMBED).status_code == 200
    assert client.delete(_api(org, "captable-round", pk=rid), **EMBED).status_code == 200


def test_api_is_org_scoped(client, venture, org_factory):
    org, admin, _ = venture
    other = org_factory(slug="other")
    rnd = services.add_round(
        other, name="Theirs", date="2026-10-01", post_money_valuation="1000000"
    )
    client.force_login(admin.user)
    r = client.patch(
        _api(org, "captable-round", pk=rnd.pk),
        {"name": "Mine"},
        content_type="application/json",
        **EMBED,
    )
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# OCF export
# --------------------------------------------------------------------------- #
def _seeded_export(org, admin, member):
    _seed_pie(org, admin, member)
    seed = services.add_round(
        org, name="Seed", date="2026-10-01", instrument="safe", post_money_valuation="1000000"
    )
    services.add_investment(
        org,
        seed,
        holder=ExternalHolder.objects.create(slug="fund", display_name="Fund"),
        amount="100000",
    )
    a = services.add_round(
        org,
        name="A",
        date="2026-11-01",
        instrument="priced",
        pre_money_valuation="4000000",
        price_per_share="2",
    )
    services.add_investment(org, a, membership=member, amount="200000")
    services.add_holding(org, membership=admin, quantity=1_000_000)


def test_ocf_package_is_coherent(client, venture):
    org, admin, member = venture
    _seeded_export(org, admin, member)
    client.force_login(member.user)
    r = client.get(_api(org, "captable-ocf"))
    assert r.status_code == 200
    files = r.json()
    manifest = files["Manifest.ocf.json"]
    assert manifest["file_type"] == "OCF_MANIFEST_FILE"
    assert manifest["issuer"]["legal_name"] == "Venture"

    tx = files["Transactions.ocf.json"]["items"]
    kinds = sorted(t["object_type"] for t in tx)
    # two pool shares + the Seed SAFE are convertibles; Series A and stock of record are stock.
    assert kinds.count("TX_CONVERTIBLE_ISSUANCE") == 3
    assert kinds.count("TX_STOCK_ISSUANCE") == 2
    safe = next(t for t in tx if t.get("convertible_type") == "SAFE")
    mech = safe["conversion_triggers"][0]["conversion_right"]["conversion_mechanism"]
    assert mech["type"] == "SAFE_CONVERSION"
    assert mech["conversion_valuation_cap"] == {"amount": "1000000", "currency": "USD"}
    assert safe["investment_amount"] == {"amount": "100000", "currency": "USD"}
    pool = [t for t in tx if t.get("convertible_type") == "CONVERTIBLE_SECURITY"]
    pcts = sorted(
        Decimal(
            t["conversion_triggers"][0]["conversion_right"]["conversion_mechanism"][
                "converts_to_percent"
            ]
        )
        for t in pool
    )
    assert pcts == [Decimal("0.22"), Decimal("0.33")]
    priced = next(t for t in tx if t["custom_id"].startswith("PS-"))
    assert priced["quantity"] == "100000"

    stakeholder_ids = {s["id"] for s in files["Stakeholders.ocf.json"]["items"]}
    assert {t["stakeholder_id"] for t in tx} <= stakeholder_ids
    class_ids = {c["id"] for c in files["StockClasses.ocf.json"]["items"]}
    assert {t["stock_class_id"] for t in tx if "stock_class_id" in t} <= class_ids
    assert [f["name"] for f in files["Financings.ocf.json"]["items"]] == ["Seed", "A"]


def test_ocf_zip_md5s_match(client, venture):
    org, admin, member = venture
    _seeded_export(org, admin, member)
    client.force_login(admin.user)
    r = client.get(_api(org, "captable-ocf") + "?as=zip")
    assert r.status_code == 200 and r["Content-Type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    manifest = json.loads(zf.read("Manifest.ocf.json"))
    for key, refs in manifest.items():
        if key.endswith("_files"):
            for ref in refs:
                data = zf.read(ref["filepath"].lstrip("./"))
                assert hashlib.md5(data).hexdigest() == ref["md5"]
    assert set(package_bytes({"x": {}}).keys()) == {"x"}


# --------------------------------------------------------------------------- #
# The page
# --------------------------------------------------------------------------- #
def test_page_member_reads_admin_edits(client, venture):
    org, admin, member = venture
    _seed_pie(org, admin, member)
    services.add_round(org, name="Seed", date="2026-10-01", post_money_valuation="1000000")
    url = reverse("captable:index", kwargs={"org_slug": org.slug})

    client.force_login(member.user)
    r = client.get(url)
    assert r.status_code == 200
    html = r.content.decode()
    assert "Founders pool" in html and "Not yet sold" in html and "100.00%" in html
    assert 'name="founders_pool_pct"' not in html
    assert 'href="/o/venture/captable/"' in html  # the tab

    client.force_login(admin.user)
    html = client.get(url).content.decode()
    assert 'name="founders_pool_pct"' in html and "Add round" in html and "Add investment" in html

    r = client.post(
        reverse("captable:save_settings", kwargs={"org_slug": org.slug}),
        {"founders_pool_pct": "50"},
    )
    assert r.status_code == 302
    assert services.get_settings(org).founders_pool_pct == Decimal("50.00")

    rnd = Round.objects.get()
    r = client.post(
        reverse("captable:add_investment", kwargs={"org_slug": org.slug}),
        {
            "holder": "",
            "holder_name": "Some Fund",
            "holder_type": "INSTITUTION",
            "amount": "50000",
            "round": rnd.pk,
        },
    )
    assert r.status_code == 302
    inv = Investment.objects.get()
    assert inv.holder.stakeholder_type == "INSTITUTION" and inv.amount == Decimal("50000")

    # A refused write is a message, not a crash.
    r = client.post(
        reverse("captable:add_investment", kwargs={"org_slug": org.slug}),
        {"holder": f"m:{member.pk}", "amount": "999999", "round": rnd.pk},
        follow=True,
    )
    assert "tranche" in r.content.decode()
    assert Investment.objects.count() == 1


def test_page_member_cannot_post(client, venture):
    org, _, member = venture
    client.force_login(member.user)
    client.post(
        reverse("captable:save_settings", kwargs={"org_slug": org.slug}),
        {"founders_pool_pct": "10"},
    )
    assert services.get_settings(org).founders_pool_pct == Decimal("55.00")


def test_dates_parse(venture):
    org, _, _ = venture
    rnd = services.add_round(org, name="Seed", date="2026-10-01", post_money_valuation="1")
    assert Round.objects.get(pk=rnd.pk).date == date(2026, 10, 1)
