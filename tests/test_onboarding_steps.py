"""
The setup dialog (<govkit-onboarding> in static/embed/govkit.js) links into GovKit pages.

Pinned down here: an admin's dialog opens on the list of the five decisions; every GovKit
path a step links to answers the person that step is for (an admin for the setup steps,
a member for the five worker steps), and every #anchor a step points at is on that page;
each decision saves through the endpoint it calls. A renamed page or a dropped id breaks
a step silently in the browser; it breaks here instead.
"""

import re
from pathlib import Path

import pytest

from django.urls import reverse

from apps.captable.models import CapTableSettings
from apps.orgs.models import MembershipRole, PiePhase

JS = Path(__file__).resolve().parent.parent / "static" / "embed" / "govkit.js"


def _steps_source():
    src = JS.read_text()
    start = src.index("function onboardingSteps(")
    end = src.index("function personKey(")
    body = src[start:end]
    split = body.index("var unit = cap.unit_name;")
    return body[:split], body[split:]


EMBED = {"HTTP_X_GOVKIT_EMBED": "1"}

DECISIONS = [
    "What this cap table holds",
    "Founders pool",
    "Starting split",
    "First valuation",
    "The first priced round",
]


def _paths(block):
    return re.findall(r"gk \+ '(/[^']*)'", block)


@pytest.fixture
def venture(org_factory, user_factory, membership_factory):
    org = org_factory(slug="venture", display_name="Venture")
    admin = user_factory(email="admin@example.com")
    member = user_factory(email="member@example.com")
    membership_factory(org=org, user=admin, role=MembershipRole.ADMIN)
    membership_factory(org=org, user=member)
    return org, admin, member


def _check(client, org, paths):
    assert paths
    for p in paths:
        page, _, anchor = p.partition("#")
        r = client.get(f"/o/{org.slug}{page}")
        assert r.status_code == 200, (p, r.status_code)
        if anchor:
            assert f'id="{anchor}"' in r.content.decode(), p


def test_admin_steps_link_to_pages_the_admin_can_open(client, venture):
    org, admin, _ = venture
    lead, _ = _steps_source()
    client.force_login(admin)
    _check(client, org, _paths(lead))


def test_worker_steps_link_to_pages_a_member_can_open(client, venture):
    org, _, member = venture
    _, worker = _steps_source()
    client.force_login(member)
    _check(client, org, _paths(worker))


def test_pie_page_says_money_does_not_vote_and_links_the_cap_table(client, venture):
    org, admin, _ = venture
    org.pie_phase = PiePhase.LAUNCHED
    org.save(update_fields=["pie_phase"])
    client.force_login(admin)
    html = client.get(f"/o/{org.slug}/pie/").content.decode()
    assert "preferred shares in the SAFE" not in html
    assert "Money doesn't vote." in html
    assert f'href="/o/{org.slug}/captable/">total valuation</a>' in html


def test_admin_dialog_opens_on_the_list_of_five_decisions(client, venture):
    lead, worker = _steps_source()
    first = lead.index("map: true")
    assert first < lead.index("decision: true")
    assert "title: 'Five decisions'" in lead
    names = re.findall(r"decision: true, name: '([^']*)'", lead)
    assert names == DECISIONS
    # Facts come after the decisions, marked as facts.
    assert lead.rindex("decision: true") < lead.index("fact: true")
    assert re.findall(r"fact: true, name: '([^']*)'", lead) == [
        "The company",
        "Your documents",
        "Invite your team",
        "Where the cap table lives",
    ]
    # Every step reaches the list again, and the list shows progress.
    src = JS.read_text()
    assert "'Back to the list'" in src
    assert "' of ' + decisions.length + ' decided'" in src
    # The worker's steps have no list and no decisions.
    assert "map: true" not in worker and "decision: true" not in worker


def _patch(client, org, body):
    return client.patch(
        reverse("captable-settings", kwargs={"org_slug": org.slug}),
        body,
        content_type="application/json",
        **EMBED,
    )


def test_each_decision_the_dialog_saves_saves_as_admin(client, venture):
    org, admin, _ = venture
    client.force_login(admin)
    summary = client.get(reverse("captable-summary", kwargs={"org_slug": org.slug})).json()
    # Nothing saved yet: the list marks each value as the default.
    assert summary["settings_saved"] is False
    assert summary["founders_pool_pct"] == "55.00"
    assert summary["pool_dilutes_at_priced_round"] is False
    assert [c[0] for c in summary["choices"]["pool_dilutes_at_priced_round"]] == ["false", "true"]
    assert "scope" in summary["choices"] and "instrument" in summary["choices"]
    # The starting split's answer comes from the pie summary.
    assert client.get(f"/api/v1/pie/orgs/{org.slug}/summary/").status_code == 200

    for body, field, want in [
        ({"scope": "POOL_ONLY"}, "scope", "POOL_ONLY"),
        ({"founders_pool_pct": "60"}, "founders_pool_pct", "60.00"),
        ({"pool_dilutes_at_priced_round": True}, "pool_dilutes_at_priced_round", True),
    ]:
        r = _patch(client, org, body)
        assert r.status_code == 200, r.content
        assert r.json()[field] == want
        assert r.json()["settings_saved"] is True
    assert CapTableSettings.objects.get(org=org).pool_dilutes_at_priced_round is True


def test_first_priced_round_choice_is_recorded_only(client, venture):
    org, admin, member = venture
    client.force_login(admin)
    before = client.get(reverse("captable-summary", kwargs={"org_slug": org.slug})).json()
    _patch(client, org, {"pool_dilutes_at_priced_round": True})
    after = client.get(reverse("captable-summary", kwargs={"org_slug": org.slug})).json()
    for k in ("founders_pool_pct", "investor_pool_pct", "lines", "total_pct"):
        assert before[k] == after[k]
    # Shown, and saved, on the cap table page's settings.
    html = client.get(f"/o/{org.slug}/captable/").content.decode()
    assert 'name="pool_dilutes_at_priced_round"' in html
    assert '<option value="true" selected>The founders pool too</option>' in html
    r = client.post(
        f"/o/{org.slug}/captable/settings/", {"pool_dilutes_at_priced_round": "false"}
    )
    assert r.status_code == 302
    assert CapTableSettings.objects.get(org=org).pool_dilutes_at_priced_round is False
    # A member cannot change it.
    client.force_login(member)
    assert _patch(client, org, {"pool_dilutes_at_priced_round": True}).status_code == 403


def _docs(org):
    return reverse("captable-documents", kwargs={"org_slug": org.slug})


def test_documents_admins_add_members_view(client, venture, monkeypatch):
    from apps.commons import storage
    from django.core.files.uploadedfile import SimpleUploadedFile

    org, admin, member = venture
    client.force_login(admin)
    r = client.post(
        _docs(org),
        {"kind": "BYLAWS", "url": "https://files.example.org/bylaws.pdf"},
        content_type="application/json",
        **EMBED,
    )
    assert r.status_code == 201, r.content
    doc = r.json()["documents"][0]
    assert doc["title"] == "Bylaws" and doc["kind_label"] == "Bylaws"

    # No bucket on this server: the upload is refused in words, nothing saved.
    pdf = SimpleUploadedFile("safe.pdf", b"%PDF-1.4 x", content_type="application/pdf")
    r = client.post(_docs(org), {"kind": "SAFE", "file": pdf}, **EMBED)
    assert r.status_code == 400 and "Paste a link" in r.json()["detail"]

    # With a bucket, the file goes there and its URL is the row's.
    monkeypatch.setattr(storage, "configured", lambda: True)
    monkeypatch.setattr(
        storage, "store_file", lambda upload, prefix: f"https://bucket.example/{prefix}/x.pdf"
    )
    pdf = SimpleUploadedFile("safe.pdf", b"%PDF-1.4 x", content_type="application/pdf")
    r = client.post(_docs(org), {"kind": "SAFE", "title": "Rolling SAFE", "file": pdf}, **EMBED)
    assert r.status_code == 201, r.content
    safe = next(d for d in r.json()["documents"] if d["kind"] == "SAFE")
    assert safe["url"] == "https://bucket.example/cap-documents/venture/x.pdf"
    assert safe["uploaded_by"]

    html = client.get(f"/o/{org.slug}/captable/").content.decode()
    assert 'id="documents"' in html and 'type="file" name="file"' in html

    client.force_login(member)
    assert [d["title"] for d in client.get(_docs(org)).json()["documents"]] == [
        "Bylaws",
        "Rolling SAFE",
    ]
    html = client.get(f"/o/{org.slug}/captable/").content.decode()
    assert "Rolling SAFE" in html and "captable/documents/add/" not in html
    r = client.post(
        _docs(org), {"url": "https://x.example/a.pdf"}, content_type="application/json", **EMBED
    )
    assert r.status_code == 403
    r = client.delete(
        reverse("captable-document", kwargs={"org_slug": org.slug, "pk": safe["document_id"]}),
        **EMBED,
    )
    assert r.status_code == 403

    client.force_login(admin)
    r = client.post(f"/o/{org.slug}/captable/documents/{safe['document_id']}/delete/")
    assert r.status_code == 302
    assert [d["title"] for d in client.get(_docs(org)).json()["documents"]] == ["Bylaws"]
