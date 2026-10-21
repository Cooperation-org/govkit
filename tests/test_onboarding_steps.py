"""
The setup dialog (<govkit-onboarding> in static/embed/govkit.js) links into GovKit pages.

Pinned down here: every GovKit path a step links to answers the person that step is for
(an admin for the eight setup steps, a member for the five worker steps), and every
#anchor a step points at is on that page. A renamed page or a dropped id breaks a step
silently in the browser; it breaks here instead.
"""

import re
from pathlib import Path

import pytest

from apps.orgs.models import MembershipRole, PiePhase

JS = Path(__file__).resolve().parent.parent / "static" / "embed" / "govkit.js"


def _steps_source():
    src = JS.read_text()
    start = src.index("function onboardingSteps(")
    end = src.index("function personKey(")
    body = src[start:end]
    split = body.index("var unit = cap.unit_name;")
    return body[:split], body[split:]


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
