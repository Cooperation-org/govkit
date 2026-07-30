"""
Task-source sync + valuation services.

`sync_source` is the reusable core: fetch eligible tasks from a tracker (via the adapter),
map each assignee to a Membership, apply the org's valuation mode, and upsert
`TrackedTask` rows keyed on the unique ``(org, source, external_id)``. It is idempotent:
re-running updates rows in place instead of duplicating.

Identity mapping (`_IdentityMap`): the map stored on the Membership first, then the email
the tracker holds for that account matched against the member's own account email. Nobody
is matched on a name or a guess, and a match is written back so the pairing becomes an
ordinary value a steward can see and correct.

`refresh_all` is the periodic entry point a scheduler/cron can call (this module does NOT
build the scheduler). The management command `sync_tasksource <org_slug>` wraps `sync_org`.

Valuation modes (selected by the org's ValuationConfig.valuation_mode):
  * direct_value: sum the numbers matched by the configured tag regex (legacy
    ``(\\d+)\\s*cook`` case-insensitive) -> claimed_value.
  * hours_rate: take the resolved hours (native points or a custom attribute) and any
    attached cash off the task -> hours, cash.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from django.db import transaction
from django.utils import timezone

from apps.orgs.models import Membership, Org, ValuationMode

from .adapters import TaskDTO, get_adapter
from .models import TaskSourceConfig, TrackedTask

logger = logging.getLogger(__name__)


# --- valuation ------------------------------------------------------------------------


def parse_direct_value(tags, pattern: str) -> Optional[Decimal]:
    """Sum the numeric group of every tag matching ``pattern`` (case-insensitive).

    Ports the legacy ``re.search(r'(\\d+)\\s*cook', tag, re.IGNORECASE)`` over story tags,
    summing across multiple matching tags. Returns None when no tag matches (so the task
    surfaces in the missing-value queue rather than dropping as zero).
    """
    if not pattern:
        return None
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error:
        logger.warning("Invalid value_tag_pattern %r; skipping direct-value parse", pattern)
        return None

    total = 0
    matched = False
    for tag in tags or []:
        match = compiled.search(str(tag))
        if match and match.groups():
            try:
                total += int(match.group(1))
                matched = True
            except (TypeError, ValueError):
                continue
    return Decimal(total) if matched else None


# --- identity mapping -----------------------------------------------------------------


class _IdentityMap:
    """Membership lookup for the tracker accounts seen on one org's tasks.

    Two ways in, in order:

    1. The map already stored on the Membership (``taiga_user_id`` / ``taiga_username``).
       A steward-set value always wins.
    2. The email the TRACKER holds for that account, matched against the member's own
       account email. The tracker's member list is where the email comes from — a task
       payload carries none — so a person is matched only when both systems show the
       same address. An email that belongs to more than one member is never matched: two
       candidates is not an answer.

    A match found by email is written back onto the Membership, so the pairing is made
    once and is then a visible, correctable value rather than something re-derived on
    every sync. An account that matches neither way resolves to None: the task is still
    tracked, just unassigned.
    """

    #: marks an email shared by several memberships — ambiguous, so never matched.
    _AMBIGUOUS = object()

    def __init__(self, org, tracker_members=None):
        self._by_user_id: dict[int, Membership] = {}
        self._by_username: dict[str, Membership] = {}
        self._by_email: dict = {}
        for m in Membership.objects.filter(org=org).select_related("user"):
            if m.taiga_user_id is not None:
                self._by_user_id[m.taiga_user_id] = m
            if m.taiga_username:
                self._by_username[m.taiga_username.lower()] = m
            email = (getattr(m.user, "email", "") or "").strip().lower()
            if email:
                self._by_email[email] = self._AMBIGUOUS if email in self._by_email else m
        self._tracker: dict[int, object] = {tm.user_id: tm for tm in (tracker_members or [])}

    def resolve(self, dto: TaskDTO) -> Optional[Membership]:
        if dto.assignee_user_id is not None and dto.assignee_user_id in self._by_user_id:
            return self._by_user_id[dto.assignee_user_id]
        if dto.assignee_username:
            membership = self._by_username.get(dto.assignee_username.lower())
            if membership is not None:
                return membership
        return self._match_by_email(dto)

    def _match_by_email(self, dto: TaskDTO) -> Optional[Membership]:
        tracker_member = self._tracker.get(dto.assignee_user_id)
        if tracker_member is None or not tracker_member.email:
            return None
        membership = self._by_email.get(tracker_member.email.strip().lower())
        if membership is None or membership is self._AMBIGUOUS:
            return None
        self._adopt(membership, tracker_member)
        return membership

    def _adopt(self, membership: Membership, tracker_member) -> None:
        """Store an email match on the Membership so it is settled from here on.

        Only fills what is empty — a value a steward typed is never overwritten.
        """
        fields = []
        if membership.taiga_user_id is None:
            membership.taiga_user_id = tracker_member.user_id
            self._by_user_id[tracker_member.user_id] = membership
            fields.append("taiga_user_id")
        if not membership.taiga_username and tracker_member.username:
            membership.taiga_username = tracker_member.username
            self._by_username[tracker_member.username.lower()] = membership
            fields.append("taiga_username")
        if fields:
            membership.save(update_fields=fields)


# --- sync -----------------------------------------------------------------------------


@dataclass
class SyncResult:
    source_id: Optional[int] = None
    fetched: int = 0
    created: int = 0
    updated: int = 0
    unassigned: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def synced(self) -> int:
        return self.created + self.updated


def _valuation_fields(dto: TaskDTO, mode: str, source: TaskSourceConfig) -> dict:
    """Compute the persisted value/hours/cash for a task under the active valuation mode."""
    if mode == ValuationMode.DIRECT_VALUE:
        return {
            "claimed_value": parse_direct_value(dto.tags, source.value_tag_pattern),
            "hours": None,
            "cash": None,
        }
    # hours_rate (default): earned value derives from hours x member rate at drop time.
    return {
        "claimed_value": None,
        "hours": dto.hours,
        "cash": dto.cash,
    }


def _fetch_tracker_members(adapter, result: SyncResult) -> list:
    """The tracker's member list for the email match, or [] if it cannot be had.

    Never fatal: a tracker that will not list its users, or one that errors on the
    attempt, leaves identity mapping exactly as it was before — steward-set only. The
    tasks still sync; some just stay unassigned, and the reason is on the result.
    """
    try:
        return adapter.fetch_members()
    except NotImplementedError:
        return []
    except Exception as exc:  # a member-list failure must not lose the whole sync
        msg = f"could not read the tracker's member list ({exc}); matching by email is off"
        logger.warning("source %s: %s", getattr(adapter.config, "pk", "?"), msg)
        result.errors.append(msg)
        return []


def sync_source(source: TaskSourceConfig) -> SyncResult:
    """Fetch + upsert TrackedTasks for one TaskSourceConfig. Idempotent."""
    result = SyncResult(source_id=source.pk)
    org = source.org
    mode = org.valuation_config.valuation_mode
    now = timezone.now()

    # Safety: direct_value with no configured pattern must NOT guess. We skip value
    # parsing (tasks land in the missing-value queue) and surface a clear config warning
    # so a steward sets a unit-specific pattern (e.g. r"(\d+)\s*cook") rather than
    # silently miscounting any number in any tag.
    if mode == ValuationMode.DIRECT_VALUE and not (source.value_tag_pattern or "").strip():
        msg = (
            "value_tag_pattern is not configured for this direct_value source; "
            "tasks are surfaced as missing-value until a pattern is set."
        )
        logger.warning("source %s: %s", source.pk, msg)
        result.errors.append(msg)

    # L7: run the tracker HTTP fetch OUTSIDE any DB transaction. A slow or hung tracker
    # must never hold a Postgres transaction (and its row locks) open across network I/O.
    adapter = get_adapter(source)
    dtos = adapter.fetch_tasks()
    result.fetched = len(dtos)
    tracker_members = _fetch_tracker_members(adapter, result)

    # Only the DB upserts run inside the transaction.
    with transaction.atomic():
        identities = _IdentityMap(org, tracker_members)
        for dto in dtos:
            assignee = identities.resolve(dto)
            if assignee is None:
                result.unassigned += 1
            defaults = {
                "external_url": dto.external_url,
                "subject": dto.subject,
                "status": dto.status_slug,
                "assignee": assignee,
                "fetched_at": now,
                **_valuation_fields(dto, mode, source),
            }
            _, created = TrackedTask.objects.update_or_create(
                org=org,
                source=source,
                external_id=dto.external_id,
                defaults=defaults,
            )
            if created:
                result.created += 1
            else:
                result.updated += 1
    return result


def sync_org(org: Org) -> list[SyncResult]:
    """Sync every task source configured for an org.

    Source by source, never all-or-nothing: one tracker being down or misconfigured
    records the failure on that source's own result and the others still sync. Without
    this the first source that raises would abort the rest, so a single dead tracker
    would silently stop an org's whole task pull.
    """
    results = []
    for source in TaskSourceConfig.objects.for_org(org):
        try:
            results.append(sync_source(source))
        except Exception as exc:  # noqa: BLE001
            logger.warning("source %s: sync failed (%s)", source.pk, exc, exc_info=True)
            results.append(SyncResult(source_id=source.pk, errors=[str(exc)]))
    return results


def refresh_all() -> list[SyncResult]:
    """Periodic-refresh entry point across all orgs (call from a scheduler/cron).

    This module deliberately does NOT own the scheduler — it just exposes the callable.
    """
    results: list[SyncResult] = []
    for org in Org.objects.all():
        results.extend(sync_org(org))
    return results


# --- missing-value queue --------------------------------------------------------------


def missing_value_tasks(org):
    """Done tasks that lack both a direct value and hours (drives steward review).

    Mirrors ``TrackedTask.is_missing_value`` as a queryset so a page/endpoint can list
    the org's tasks that still need a value before they can drop.
    """
    return (
        TrackedTask.objects.for_org(org)
        .filter(claimed_value__isnull=True, hours__isnull=True)
        .select_related("assignee", "assignee__user", "source")
    )
