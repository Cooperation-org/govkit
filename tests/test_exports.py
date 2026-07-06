"""
Tests for apps.exports — opening-balance import + generic/Slicing-Pie export.

Fixtures (user_factory / org_factory / membership_factory) come from tests/conftest.py.
"""

import csv
import io
from decimal import Decimal

import pytest

from apps.drops.models import DropLine, DropRun, DropRunState
from apps.exports import services
from apps.exports.exporters import get_exporter, membership_totals
from apps.exports.models import ImportBatch
from apps.orgs.models import MembershipRole, OpeningBalance
from apps.tasksources.models import TaskSourceConfig, TrackedTask


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _csv_file(text, name="opening_balances.csv"):
    buf = io.BytesIO(text.encode("utf-8"))
    buf.name = name
    return buf


def _member(org, membership_factory, user_factory, email=None, **kwargs):
    user = user_factory(email=email)
    return membership_factory(org, user, **kwargs)


def _issued_line(org, membership, value, tasks=None, computed=None, adjustment="0", reason=""):
    """Create a DropLine inside an APPROVED run (i.e. issued equity)."""
    run = DropRun.objects.create(org=org, state=DropRunState.OPEN)
    line = DropLine(
        org=org,
        run=run,
        membership=membership,
        computed_value=Decimal(computed if computed is not None else value),
        adjustment=Decimal(adjustment),
        adjustment_reason=reason,
        final_value=Decimal(value),
    )
    line.save()
    if tasks:
        line.tasks.set(tasks)
    run.state = DropRunState.APPROVED
    from django.utils import timezone

    run.approved_at = timezone.now()
    run.save(update_fields=["state", "approved_at"])
    return line


# --------------------------------------------------------------------------- #
# import
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_import_creates_balances_and_batch(org_factory, membership_factory, user_factory):
    org = org_factory()
    m1 = _member(org, membership_factory, user_factory, email="member1@example.org")
    m2 = _member(org, membership_factory, user_factory, email="member2@example.org")

    text = (
        "member_email,value,source_note\n"
        "member1@example.org,1200.00,issued_cook history\n"
        "member2@example.org,850.50,issued_cook history\n"
    )
    result = services.import_opening_balances(org, _csv_file(text), created_by=m1.user)

    assert result.ok
    assert result.created == 2
    assert OpeningBalance.objects.filter(org=org, membership=m1).get().value == Decimal("1200.00")
    assert OpeningBalance.objects.filter(org=org, membership=m2).get().value == Decimal("850.50")

    batch = result.batch
    assert isinstance(batch, ImportBatch)
    assert batch.org == org
    assert batch.row_count == 2
    assert batch.created_by == m1.user
    assert batch.filename == "opening_balances.csv"
    assert "mode=replace" in batch.notes


@pytest.mark.django_db
def test_import_maps_by_taiga_id(org_factory, membership_factory, user_factory):
    org = org_factory()
    m = _member(
        org, membership_factory, user_factory, email="member1@example.org", taiga_user_id=42
    )

    text = "taiga_user_id,value\n42,300\n"
    result = services.import_opening_balances(org, _csv_file(text))

    assert result.created == 1
    assert OpeningBalance.objects.get(org=org, membership=m).value == Decimal("300")


@pytest.mark.django_db
def test_import_reports_bad_rows_but_imports_good(org_factory, membership_factory, user_factory):
    org = org_factory()
    _member(org, membership_factory, user_factory, email="member1@example.org")

    text = (
        "member_email,value\n"
        "member1@example.org,100\n"  # ok
        "nobody@example.org,50\n"  # unknown member
        "member1@example.org,abc\n"  # non-numeric
        "member1@example.org,\n"  # missing value
        ",25\n"  # no identifier
    )
    result = services.import_opening_balances(org, _csv_file(text))

    assert result.created == 1
    assert len(result.errors) == 4
    messages = " ".join(e.message for e in result.errors)
    assert "no membership with email" in messages
    assert "not a number" in messages
    assert "missing value" in messages
    assert "no identifier" in messages
    # A batch is still written (auditable), reflecting only the valid row.
    assert result.batch.row_count == 1


@pytest.mark.django_db
def test_import_ambiguous_identifiers_rejected(org_factory, membership_factory, user_factory):
    org = org_factory()
    _member(org, membership_factory, user_factory, email="member1@example.org", taiga_user_id=1)
    _member(org, membership_factory, user_factory, email="member2@example.org", taiga_user_id=2)

    text = "member_email,taiga_user_id,value\nmember1@example.org,2,100\n"
    result = services.import_opening_balances(org, _csv_file(text))

    assert result.created == 0
    assert "resolve to different members" in result.errors[0].message


@pytest.mark.django_db
def test_import_replace_is_idempotent(org_factory, membership_factory, user_factory):
    org = org_factory()
    m = _member(org, membership_factory, user_factory, email="member1@example.org")
    text = "member_email,value\nmember1@example.org,500\n"

    services.import_opening_balances(org, _csv_file(text))
    services.import_opening_balances(org, _csv_file(text))  # re-run

    balances = OpeningBalance.objects.filter(org=org, membership=m)
    assert balances.count() == 1
    assert balances.get().value == Decimal("500")


@pytest.mark.django_db
def test_import_replace_corrects_value(org_factory, membership_factory, user_factory):
    org = org_factory()
    m = _member(org, membership_factory, user_factory, email="member1@example.org")
    services.import_opening_balances(
        org, _csv_file("member_email,value\nmember1@example.org,500\n")
    )
    services.import_opening_balances(
        org, _csv_file("member_email,value\nmember1@example.org,900\n")
    )

    balances = OpeningBalance.objects.filter(org=org, membership=m)
    assert balances.count() == 1
    assert balances.get().value == Decimal("900")


@pytest.mark.django_db
def test_import_append_skips_exact_duplicates(org_factory, membership_factory, user_factory):
    org = org_factory()
    m = _member(org, membership_factory, user_factory, email="member1@example.org")
    text = "member_email,value,source_note\nmember1@example.org,500,seed\n"

    services.import_opening_balances(org, _csv_file(text), mode=services.MODE_APPEND)
    result = services.import_opening_balances(org, _csv_file(text), mode=services.MODE_APPEND)

    assert result.created == 0
    assert result.skipped == 1
    assert OpeningBalance.objects.filter(org=org, membership=m).count() == 1


@pytest.mark.django_db
def test_import_scopes_to_org(org_factory, membership_factory, user_factory):
    """An email belonging to a member of a different org is not resolvable."""
    org_a = org_factory(slug="a")
    org_b = org_factory(slug="b")
    _member(org_b, membership_factory, user_factory, email="member1@example.org")

    result = services.import_opening_balances(
        org_a, _csv_file("member_email,value\nmember1@example.org,100\n")
    )
    assert result.created == 0
    assert "no membership" in result.errors[0].message


@pytest.mark.django_db
def test_import_missing_value_column_raises(org_factory):
    org = org_factory()
    with pytest.raises(services.CsvImportError):
        services.import_opening_balances(org, _csv_file("member_email,amount\nx@example.org,5\n"))
    assert ImportBatch.objects.for_org(org).count() == 0


# --------------------------------------------------------------------------- #
# generic export
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_generic_export_totals_and_share(org_factory, membership_factory, user_factory):
    org = org_factory()
    m1 = _member(org, membership_factory, user_factory, email="member1@example.org")
    m2 = _member(org, membership_factory, user_factory, email="member2@example.org")

    _issued_line(org, m1, "60")  # issued drop
    OpeningBalance.objects.create(org=org, membership=m1, value=Decimal("40"))  # -> m1 total 100
    _issued_line(org, m2, "100")  # -> m2 total 100

    rows = {r["membership"]: r for r in membership_totals(org)}
    assert rows[m1]["total_value"] == Decimal("100")
    assert rows[m1]["share"] == Decimal("0.5")

    csv_text = get_exporter("generic").to_csv(org)
    parsed = list(csv.DictReader(io.StringIO(csv_text)))
    assert parsed[0].keys() >= {
        "member_email",
        "issued_value",
        "opening_balance",
        "total_value",
        "share",
        "unit",
    }
    by_email = {r["member_email"]: r for r in parsed}
    assert by_email["member1@example.org"]["issued_value"] == "60.00"
    assert by_email["member1@example.org"]["opening_balance"] == "40.00"
    assert by_email["member1@example.org"]["total_value"] == "100.00"
    assert by_email["member1@example.org"]["share"] == "0.500000"
    assert by_email["member1@example.org"]["unit"] == org.unit_name


@pytest.mark.django_db
def test_generic_export_excludes_zero_equity_members(org_factory, membership_factory, user_factory):
    org = org_factory()
    _member(org, membership_factory, user_factory, email="member1@example.org")  # no equity
    m2 = _member(org, membership_factory, user_factory, email="member2@example.org")
    _issued_line(org, m2, "10")

    parsed = list(csv.DictReader(io.StringIO(get_exporter("generic").to_csv(org))))
    assert [r["member_email"] for r in parsed] == ["member2@example.org"]


@pytest.mark.django_db
def test_open_run_lines_are_not_issued(org_factory, membership_factory, user_factory):
    """Only APPROVED runs count as issued equity."""
    org = org_factory()
    m = _member(org, membership_factory, user_factory, email="member1@example.org")
    run = DropRun.objects.create(org=org, state=DropRunState.OPEN)
    DropLine(org=org, run=run, membership=m, final_value=Decimal("99")).save()

    assert membership_totals(org) == []


# --------------------------------------------------------------------------- #
# slicing pie export
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_slicing_pie_columns_cash_offset_and_multipliers(
    org_factory, membership_factory, user_factory
):
    org = org_factory()
    org.default_hourly_rate = Decimal("50.00")
    org.save()
    config = org.valuation_config
    config.at_risk_multiplier_noncash = Decimal("2.0")
    config.at_risk_multiplier_cash = Decimal("4.0")
    config.save()

    m = _member(org, membership_factory, user_factory, email="member1@example.org", taiga_user_id=7)
    source = TaskSourceConfig.objects.create(org=org, base_url="https://tracker.example")
    task = TrackedTask.objects.create(
        org=org,
        source=source,
        external_id="T-1",
        hours=Decimal("3"),
        cash=Decimal("25"),
    )
    _issued_line(org, m, "300", tasks=[task], computed="300")
    OpeningBalance.objects.create(org=org, membership=m, value=Decimal("80"), source_note="legacy")

    csv_text = get_exporter("slicing_pie").to_csv(org)
    rows = list(csv.DictReader(io.StringIO(csv_text)))

    expected_cols = {
        "member_email",
        "taiga_user_id",
        "contribution_type",
        "contribution_ref",
        "contribution_date",
        "hours",
        "rate",
        "cash_offset",
        "at_risk_multiplier_noncash",
        "at_risk_multiplier_cash",
        "base_value",
        "adjustment",
        "slices",
        "unit",
    }
    assert set(rows[0].keys()) == expected_cols

    work = next(r for r in rows if r["contribution_type"] == "work_drop")
    assert work["member_email"] == "member1@example.org"
    assert work["taiga_user_id"] == "7"
    assert work["hours"] == "3.00"
    assert work["rate"] == "50.00"
    assert work["cash_offset"] == "25.00"
    assert work["at_risk_multiplier_noncash"] == "2.000"
    assert work["at_risk_multiplier_cash"] == "4.000"
    assert work["slices"] == "300.00"

    opening = next(r for r in rows if r["contribution_type"] == "opening_balance")
    assert opening["slices"] == "80.00"
    assert "legacy" in opening["contribution_ref"]
    assert opening["hours"] == ""  # opening balances carry no hours/cash


@pytest.mark.django_db
def test_exporter_registry_lists_targets():
    from apps.exports.exporters import EXPORTERS

    assert set(EXPORTERS) >= {"generic", "slicing_pie"}
    with pytest.raises(KeyError):
        get_exporter("fairmint")  # not registered yet — the seam is open for it


# --------------------------------------------------------------------------- #
# views + API (org-scoping + role gating)
# --------------------------------------------------------------------------- #
def _upload(text, name="opening_balances.csv"):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, text.encode("utf-8"), content_type="text/csv")


@pytest.mark.django_db
def test_export_download_view(client, org_factory, membership_factory, user_factory):
    org = org_factory(slug="acme")
    m = _member(org, membership_factory, user_factory, email="member1@example.org")
    _issued_line(org, m, "10")
    client.force_login(m.user)

    resp = client.get(f"/o/{org.slug}/exports/export/generic.csv")
    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/csv"
    assert "attachment" in resp["Content-Disposition"]
    assert b"member1@example.org" in resp.content


@pytest.mark.django_db
def test_import_view_requires_steward(client, org_factory, membership_factory, user_factory):
    org = org_factory(slug="acme")
    member = _member(org, membership_factory, user_factory, role=MembershipRole.MEMBER)
    steward = _member(org, membership_factory, user_factory, role=MembershipRole.STEWARD)
    _member(org, membership_factory, user_factory, email="target@example.org")

    csv_text = "member_email,value\ntarget@example.org,150\n"

    # A plain member cannot import.
    client.force_login(member.user)
    resp = client.post(f"/o/{org.slug}/exports/import/", {"file": _upload(csv_text)})
    assert resp.status_code == 403
    assert not OpeningBalance.objects.filter(org=org).exists()

    # A steward can.
    client.force_login(steward.user)
    resp = client.post(f"/o/{org.slug}/exports/import/", {"file": _upload(csv_text)}, follow=True)
    assert resp.status_code == 200
    assert OpeningBalance.objects.filter(org=org).count() == 1
    assert ImportBatch.objects.for_org(org).count() == 1


@pytest.mark.django_db
def test_api_import_and_export(client, org_factory, membership_factory, user_factory):
    org = org_factory(slug="acme")
    steward = _member(org, membership_factory, user_factory, role=MembershipRole.STEWARD)
    _member(org, membership_factory, user_factory, email="target@example.org")
    client.force_login(steward.user)

    resp = client.post(
        f"/api/v1/exports/o/{org.slug}/batches/import_csv/",
        {"file": _upload("member_email,value\ntarget@example.org,200\n")},
    )
    assert resp.status_code == 201
    assert resp.json()["created"] == 1

    resp = client.get(f"/api/v1/exports/o/{org.slug}/batches/")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = client.get(f"/api/v1/exports/o/{org.slug}/export/generic/")
    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/csv"
    assert b"target@example.org" in resp.content


@pytest.mark.django_db
def test_api_import_forbidden_for_member(client, org_factory, membership_factory, user_factory):
    org = org_factory(slug="acme")
    member = _member(org, membership_factory, user_factory, role=MembershipRole.MEMBER)
    _member(org, membership_factory, user_factory, email="target@example.org")
    client.force_login(member.user)

    resp = client.post(
        f"/api/v1/exports/o/{org.slug}/batches/import_csv/",
        {"file": _upload("member_email,value\ntarget@example.org,200\n")},
    )
    assert resp.status_code == 403
    assert not OpeningBalance.objects.filter(org=org).exists()


@pytest.mark.django_db
def test_api_non_member_blocked(client, org_factory, membership_factory, user_factory):
    """OrgContextMiddleware enforces membership on the API prefix too."""
    org = org_factory(slug="acme")
    outsider = user_factory(email="outsider@example.org")
    client.force_login(outsider)

    resp = client.get(f"/api/v1/exports/o/{org.slug}/batches/")
    assert resp.status_code == 403
