"""
The team CRM, read straight from its database, for the dash's Reach out and
Deals cards.

The dash's own <crm-reachout> card asks the CRM with the CRM's session cookie,
so a member signed in to workers.vc but not yet to their team's CRM saw a blank
card. GovKit already knows the member (its session rides every dash fetch), so
it reads the same rows itself and the cards fill for anyone signed in.

Each team's CRM is its own Odoo database, `COMMS_CRM_DB_PATTERN` (`crm-{slug}`),
on the same Postgres server as GovKit. The connection reuses GovKit's own
database credentials with only the database name changed; that role is granted
SELECT on `crm_lead`, `crm_stage`, `res_partner`, `crm_recurring_plan`,
`res_company` and `res_currency` in each
team CRM database, and nothing else. Read only: every write stays in the CRM.

The ordering is the Outreach Runner's own (crm_outreach_runner's QUEUE_ORDER):
pinned first, then its sequence, then score.
"""

from __future__ import annotations

import logging
import psycopg
from django.conf import settings

logger = logging.getLogger(__name__)

MAX_LIMIT = 50


def _connect(org_slug: str):
    db = settings.DATABASES["default"]
    return psycopg.connect(
        dbname=settings.COMMS_CRM_DB_PATTERN.format(slug=org_slug),
        user=db.get("USER") or None,
        password=db.get("PASSWORD") or None,
        host=db.get("HOST") or None,
        port=db.get("PORT") or None,
        connect_timeout=5,
    )


def _iso(dt):
    return dt.replace(microsecond=0).isoformat() + "Z" if dt else None


def _money(v):
    return None if v is None else f"{v:.2f}"


def _text(jsonb_name):
    """Odoo 17 keeps translatable names as {"en_US": ...}."""
    if isinstance(jsonb_name, dict):
        return jsonb_name.get("en_US") or next(iter(jsonb_name.values()), "")
    return jsonb_name or ""


REACHOUT_SQL = """
    SELECT l.id, l.name, COALESCE(p.name, l.contact_name, l.partner_name, ''),
           COALESCE(l.email_from, p.email), p.linkedin,
           l.last_outreach_date, l.outreach_score, l.outreach_score_reason
      FROM crm_lead l
      LEFT JOIN res_partner p ON p.id = l.partner_id
      LEFT JOIN crm_stage s ON s.id = l.stage_id
     WHERE l.active AND l.type = 'opportunity' AND NOT COALESCE(s.is_won, false)
     ORDER BY l.outreach_pinned DESC NULLS LAST, l.outreach_seq ASC NULLS LAST,
              l.outreach_score DESC NULLS LAST, l.id
     LIMIT %s
"""

TO_REACH_SQL = """
    SELECT count(*) FROM crm_lead l LEFT JOIN crm_stage s ON s.id = l.stage_id
     WHERE l.active AND l.type = 'opportunity' AND NOT COALESCE(s.is_won, false)
       AND l.last_outreach_date IS NULL
"""

DEALS_SQL = """
    SELECT l.id, l.name, COALESCE(p.name, l.partner_name, ''), s.name, s.is_won,
           l.expected_revenue, l.recurring_revenue, rp.name, rp.number_of_months,
           l.probability, l.date_closed
      FROM crm_lead l
      LEFT JOIN res_partner p ON p.id = l.partner_id
      LEFT JOIN crm_stage s ON s.id = l.stage_id
      LEFT JOIN crm_recurring_plan rp ON rp.id = l.recurring_plan
     WHERE l.active AND l.type = 'opportunity'
       AND (COALESCE(s.is_won, false)
            OR COALESCE(l.expected_revenue, 0) > 0
            OR COALESCE(l.recurring_revenue, 0) > 0)
     ORDER BY COALESCE(s.is_won, false) DESC, l.date_closed DESC NULLS LAST, l.id DESC
"""


CURRENCY_SQL = """
    SELECT c.name FROM res_company co JOIN res_currency c ON c.id = co.currency_id
     ORDER BY co.id LIMIT 1
"""


def team_crm(org_slug: str, limit: int = 5) -> dict | None:
    """{reachout, count_to_reach, deals} for one team, or None when the team has
    no CRM database or it cannot be read. Rows carry the lead id; the page that
    knows the team's CRM address builds the link."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    try:
        with _connect(org_slug) as conn, conn.cursor() as cur:
            cur.execute(REACHOUT_SQL, (limit,))
            reach = cur.fetchall()
            cur.execute(TO_REACH_SQL)
            (to_reach,) = cur.fetchone()
            cur.execute(DEALS_SQL)
            deals = cur.fetchall()
            cur.execute(CURRENCY_SQL)
            row = cur.fetchone()
    except psycopg.Error as exc:
        logger.info("team CRM %s not readable: %s", org_slug, exc)
        return None

    return {
        "currency": row[0] if row else None,
        "count_to_reach": to_reach,
        "reachout": [
            {
                "id": r[0],
                "name": r[1] or "",
                "partner_name": r[2] or "",
                "email": r[3] or None,
                "linkedin": r[4] or None,
                "last_outreach_date": _iso(r[5]),
                "outreach_score": r[6],
                "outreach_score_reason": r[7] or "",
            }
            for r in reach
        ],
        "deals": [
            {
                "id": d[0],
                "name": d[1] or "",
                "partner_name": d[2] or "",
                "stage": _text(d[3]),
                "won": bool(d[4]),
                "expected_revenue": _money(d[5]),
                "recurring_revenue": _money(d[6]) if d[6] else None,
                "recurring_plan": _text(d[7]) or None,
                "recurring_months": d[8],
                "probability": d[9],
                "date_closed": _iso(d[10]),
            }
            for d in deals
        ],
    }

