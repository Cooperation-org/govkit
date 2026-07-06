"""
Seed a self-contained, story-telling demo org ("Sunrise Co-op (demo)").

    python manage.py seed_demo

Idempotent: re-running deletes the ``demo`` org (cascading all its domain data) and its
``@demo.test`` users, then rebuilds the whole demo from scratch through the REAL domain
services (``apps.drops.services``, ``apps.votes.services``, ``apps.sortition.services``),
so every record is in a valid, service-produced state — never a hand-written invalid one.

What it builds, and the page each row lights up:

  * 5 generic members (NO real names — email localparts are the labels), roles
    admin/steward/steward/member/member, per-member hourly_rate + taiga_username.
  * Opening balances for 2 members  -> the Pie has imported history to trace to.
  * ~10 done TrackedTasks (2 left value-less) -> the steward missing-value review queue.
  * DropRun #1: opened -> one line adjusted up (+reason) -> approved  -> issued Pie shares.
  * DropRun #2: opened, left OPEN with pending lines           -> pending-vs-issued view.
  * A live work-weighted Vote with 4 ballots                   -> weighted-vs-raw tally.
  * A seeded SortitionDraw for 2 seats                         -> reproducible Committee.

The dev-login demo account (admin ``ada@demo.test``) is given a known password so a
presenter can sign in via /accounts/dev-login/ when GOVKIT_DEV_LOGIN is enabled. This is a
throwaway demo credential, documented here on purpose.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.drops.models import DropRun
from apps.drops.services import (
    adjust_line,
    approve_run,
    open_run,
    review_queue,
)
from apps.orgs.models import (
    BudgetEnforcement,
    Membership,
    MembershipRole,
    OpeningBalance,
    Org,
    ValuationConfig,
    ValuationMode,
    WeightWindow,
)
from apps.orgs.weighting import work_weight_map
from apps.sortition.services import run_draw, selected_seats, verify_draw
from apps.tasksources.models import AdapterType, TaskSourceConfig, TrackedTask
from apps.votes.services import cast_ballot, create_vote, open_vote, tally

# --- Demo constants (throwaway demo account — safe to document/print). ------------------
ORG_SLUG = "demo"
ORG_NAME = "Sunrise Co-op (demo)"
UNIT = "COOK"
DEMO_PASSWORD = "govkit-demo-2026"  # nosec B105 - throwaway demo dev-login credential
DRAW_SEED = "sunrise-2026-committee"

# Generic members only — no real person names anywhere. (email, role, hourly_rate)
MEMBERS = [
    ("ada@demo.test", MembershipRole.ADMIN, Decimal("45.00")),
    ("blz@demo.test", MembershipRole.STEWARD, Decimal("42.00")),
    ("cy@demo.test", MembershipRole.STEWARD, Decimal("40.00")),
    ("dot@demo.test", MembershipRole.MEMBER, Decimal("38.00")),
    ("evi@demo.test", MembershipRole.MEMBER, Decimal("35.00")),
]


class Command(BaseCommand):
    help = "Seed the self-contained 'Sunrise Co-op (demo)' org (idempotent)."

    @transaction.atomic
    def handle(self, *args, **opts):
        User = get_user_model()

        # --- Idempotency: wipe any prior demo, then rebuild. ---
        existing = Org.objects.filter(slug=ORG_SLUG).first()
        if existing is not None:
            # DropLine.membership is on_delete=PROTECT, so the org-cascade can't reach
            # memberships while drop lines exist. Delete the runs first (their lines
            # cascade away), which releases the protect; then the org cascade clears the
            # rest (memberships, tasks, opening balances, votes, ballots, draws).
            DropRun.objects.filter(org=existing).delete()
            existing.delete()
        User.objects.filter(email__endswith="@demo.test").delete()

        # --- Org + valuation policy (direct_value, 1.0 multipliers, unlimited/soft). ---
        org = Org.objects.create(
            slug=ORG_SLUG,
            display_name=ORG_NAME,
            unit_name=UNIT,
            default_hourly_rate=Decimal("40.00"),
        )
        ValuationConfig.objects.create(
            org=org,
            valuation_mode=ValuationMode.DIRECT_VALUE,
            at_risk_multiplier_noncash=Decimal("1.0"),
            at_risk_multiplier_cash=Decimal("1.0"),
            weight_window=WeightWindow.ALL_TIME,
            assignment_budget_amount=None,  # unlimited
            budget_enforcement=BudgetEnforcement.SOFT,
        )

        # --- Members. display_name left blank so the UI label is the email localpart. ---
        m = {}  # email localpart -> Membership
        for email, role, rate in MEMBERS:
            local = email.split("@")[0]
            user = User.objects.create_user(email=email, password=None)
            if role == MembershipRole.ADMIN:
                user.set_password(DEMO_PASSWORD)  # presenter dev-login
                user.save(update_fields=["password"])
            m[local] = Membership.objects.create(
                org=org,
                user=user,
                role=role,
                hourly_rate=rate,
                taiga_username=local,
            )

        # --- Opening balances (imported history the Pie traces back to). ---
        OpeningBalance.objects.create(
            org=org,
            membership=m["ada"],
            value=Decimal("120"),
            source_note="migrated from prior spreadsheet",
        )
        OpeningBalance.objects.create(
            org=org,
            membership=m["blz"],
            value=Decimal("60"),
            source_note="migrated from prior spreadsheet",
        )

        # --- Task source + tracked tasks. ---
        source = TaskSourceConfig.objects.create(
            org=org,
            adapter_type=AdapterType.TAIGA,
            base_url="https://taiga.demo.test/api/v1",
            project_selector="sunrise-coop",
            value_tag_pattern=r"(\d+)\s*cook",
        )
        seq = {"n": 0}

        def task(subject, assignee, value):
            seq["n"] += 1
            n = seq["n"]
            return TrackedTask.objects.create(
                org=org,
                source=source,
                external_id=f"T-{n}",
                external_url=f"https://taiga.demo.test/project/sunrise-coop/task/{n}",
                subject=subject,
                assignee=assignee,
                claimed_value=(Decimal(value) if value is not None else None),
                status="done",
            )

        # Batch A — fully valued; feeds the APPROVED run (issued Pie shares).
        task("Write onboarding guide", m["ada"], 30)
        task("Set up CI pipeline", m["ada"], 18)
        task("Design landing page", m["blz"], 25)
        task("Community call notes", m["cy"], 10)
        task("Fix signup bug", m["dot"], 15)
        task("Outreach to 5 partners", m["evi"], 20)

        # --- DropRun #1: open -> adjust one line up (+reason) -> approve => ISSUED. ---
        run1 = open_run(org, opened_by_membership=m["ada"], opened_by_user=m["ada"].user)
        evi_line = run1.lines.get(membership=m["evi"])
        adjust_line(
            evi_line,
            Decimal("8"),
            "under-claimed: also did the partner follow-ups",
            adjusted_by=m["blz"],  # a steward makes the correction
        )
        approve_run(run1, approved_by_membership=m["blz"])

        # Batch B — created AFTER run1 so open_run gathers only these into run2.
        # Two valued (pending equity) + two value-less (steward missing-value queue).
        task("Update privacy policy", m["cy"], 12)
        task("Record demo video", m["dot"], 14)
        task("Triage support inbox", m["blz"], None)  # missing value
        task("Plan Q3 roadmap", m["ada"], None)  # missing value

        # --- DropRun #2: opened, left OPEN => pending lines + missing-value review. ---
        run2 = open_run(org, opened_by_membership=m["cy"], opened_by_user=m["cy"].user)
        missing_count = len(review_queue(run2)["missing_value_tasks"])

        # --- A live work-weighted vote (weights snapshot the post-run1 electorate). ---
        vote = create_vote(org, "Which focus next quarter?", ["Growth", "Product", "Community"])
        open_vote(vote)
        cast_ballot(vote, m["ada"], "Growth")
        cast_ballot(vote, m["blz"], "Product")
        cast_ballot(vote, m["dot"], "Community")
        cast_ballot(vote, m["evi"], "Growth")
        # (cy abstains — left un-cast to show turnout < electorate.)
        vote_tally = tally(vote)

        # --- A seeded, reproducible sortition draw for 2 committee seats. ---
        draw = run_draw(org, seats=2, window=WeightWindow.ALL_TIME, seed=DRAW_SEED)
        seats = selected_seats(draw)

        # --- Click-through summary. ---
        weights = work_weight_map(org, WeightWindow.ALL_TIME)
        out = self.stdout
        ok = self.style.SUCCESS
        out.write(ok("\n=== Sunrise Co-op (demo) seeded ==="))
        out.write(f"Org: {org.display_name}  slug={org.slug}  unit={org.unit_name}")
        out.write(
            "Dev-login: /accounts/login/ -> dev-login form -> "
            f"ada@demo.test / {DEMO_PASSWORD}  (admin)"
        )

        out.write(ok("\nPie / work-weight (issued drops + opening balances):"))
        for local, mem in m.items():
            out.write(f"  {local:<4} weight={weights.get(mem.id, Decimal('0'))}  ({mem.role})")

        out.write(ok("\nDrops:"))
        out.write(
            f"  Run #{run1.pk} APPROVED — {run1.lines.count()} lines "
            f"(evi adjusted +8 by a steward, reason recorded)"
        )
        out.write(
            f"  Run #{run2.pk} OPEN — {run2.lines.count()} pending lines; "
            f"missing-value review queue = {missing_count} task(s)"
        )

        out.write(ok("\nVote — 'Which focus next quarter?' (live):"))
        out.write(f"  winner (weighted): {vote_tally.winner}")
        for r in vote_tally.results:
            out.write(
                f"  {r.option:<10} weighted={r.weighted} ({r.weighted_pct}%)  "
                f"raw={r.raw} ({r.raw_pct}%)"
            )

        out.write(ok("\nCommittee — seeded sortition (2 seats):"))
        out.write(f"  seed={draw.seed}  reproducible={verify_draw(draw)}")
        for s in seats:
            out.write(f"  seat {s.seat}: {s.membership.user.get_short_name()} (weight {s.weight})")
        out.write(ok("\nDone. Re-run is safe (idempotent).\n"))
