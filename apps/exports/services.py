"""
Import/export services for the exports app.

Two boundaries:

  * Import  — a CSV of pre-existing equity becomes `OpeningBalance` rows recorded under an
    audit `ImportBatch`. "Starting a team with existing equity" IS this feature; there is
    no separate 'demo data' path. Identifiers in the CSV are mapped to `Membership`
    *explicitly* (by email or Taiga user id) — a member is never inferred or created.
  * Export  — the current pie / contribution record leaves through an `EquityExport`
    adapter (see `exporters.py`): generic CSV and Slicing Pie today, Fairmint later.

See `apps/exports/README.md` for the CSV schema and the recipe to produce the historical
CSV from the legacy `issued_cook` table (GovKit never reads that legacy DB live).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

from django.db import transaction

from apps.orgs.models import Membership, OpeningBalance

from .exporters import get_exporter  # noqa: F401 (re-exported for callers/tests)
from .models import ImportBatch, ImportKind

# CSV schema ---------------------------------------------------------------- #
# `value` is required. Each row must carry at least one resolvable identifier via
# `member_email` and/or `taiga_user_id`. `source_note` is optional provenance text.
VALUE_COLUMN = "value"
EMAIL_COLUMN = "member_email"
TAIGA_ID_COLUMN = "taiga_user_id"
NOTE_COLUMN = "source_note"

# Re-run semantics.
MODE_REPLACE = "replace"  # default: a member's opening balances are replaced by the file
MODE_APPEND = "append"  # add rows, skipping exact (member, value, note) duplicates
VALID_MODES = (MODE_REPLACE, MODE_APPEND)


@dataclass
class RowError:
    line_number: int  # 1-based CSV data line (header excluded)
    identifier: str
    message: str


@dataclass
class ImportResult:
    """Outcome of an import run. `batch` is the audit record; `errors` names bad rows."""

    batch: Optional[ImportBatch]
    created: int = 0
    skipped: int = 0
    errors: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class CsvImportError(ValueError):
    """A structural problem that prevents the import from running at all (no batch made)."""


def _decode(fileobj) -> io.StringIO:
    """Return a text stream for a bytes-or-text file-like object (utf-8, BOM-tolerant)."""
    raw = fileobj.read()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8-sig")
    else:
        raw = raw.lstrip("﻿")
    return io.StringIO(raw)


def _normalize_headers(fieldnames) -> dict:
    """Map lower/trimmed header -> actual header, so column order/case don't matter."""
    if not fieldnames:
        return {}
    return {name.strip().lower(): name for name in fieldnames if name}


def _resolve_membership(email, taiga_id, memberships_by_email, memberships_by_taiga):
    """
    Explicitly map a row's identifier(s) to a Membership. Never infers or creates one.

    Returns (membership, error_message). Exactly one of the two is non-None.
    """
    hits = {}
    if email:
        m = memberships_by_email.get(email.strip().lower())
        if m is None:
            return None, f"no membership with email '{email.strip()}'"
        hits[m.pk] = m
    if taiga_id:
        try:
            tid = int(taiga_id)
        except (TypeError, ValueError):
            return None, f"taiga_user_id '{taiga_id}' is not an integer"
        m = memberships_by_taiga.get(tid)
        if m is None:
            return None, f"no membership with taiga_user_id '{taiga_id}'"
        hits[m.pk] = m
    if not hits:
        return None, "no identifier (need member_email or taiga_user_id)"
    if len(hits) > 1:
        return None, "member_email and taiga_user_id resolve to different members"
    return next(iter(hits.values())), None


def import_opening_balances(
    org, fileobj, created_by=None, mode: str = MODE_REPLACE
) -> ImportResult:
    """
    Import opening balances from a CSV file-like object.

    Args:
        org: the target Org (rows are scoped to it; identifiers resolve within it).
        fileobj: a text or bytes file-like object containing the CSV.
        created_by: the User performing the import (recorded on the batch for audit).
        mode: MODE_REPLACE (default) — for every member named in the file, their existing
              opening balances are removed and replaced by the file's rows, so re-running
              a corrected file converges (idempotent). MODE_APPEND — add rows but skip an
              exact (member, value, source_note) duplicate, so re-running the *same* file
              is a no-op.

    Returns:
        ImportResult with the audit `ImportBatch`, counts, and per-row errors. Bad rows
        are reported (not created); valid rows are imported. Raises CsvImportError only
        for a structural failure (unreadable CSV / missing `value` column) — no batch is
        created in that case.
    """
    if mode not in VALID_MODES:
        raise CsvImportError(f"unknown mode '{mode}' (use one of {VALID_MODES})")

    stream = _decode(fileobj)
    reader = csv.DictReader(stream)
    headers = _normalize_headers(reader.fieldnames)
    if VALUE_COLUMN not in headers:
        raise CsvImportError(
            f"CSV must have a '{VALUE_COLUMN}' column (found: {reader.fieldnames})"
        )
    if EMAIL_COLUMN not in headers and TAIGA_ID_COLUMN not in headers:
        raise CsvImportError(f"CSV must have a '{EMAIL_COLUMN}' and/or '{TAIGA_ID_COLUMN}' column")

    memberships = list(Membership.objects.filter(org=org).select_related("user"))
    by_email = {m.user.email.strip().lower(): m for m in memberships}
    by_taiga = {m.taiga_user_id: m for m in memberships if m.taiga_user_id is not None}

    filename = getattr(fileobj, "name", "") or ""
    result = ImportResult(batch=None)

    def col(row, key):
        actual = headers.get(key)
        return (row.get(actual) or "").strip() if actual else ""

    # Parse + validate every row first, so structural problems surface before any write.
    valid_rows = []  # (membership, value, note)
    for offset, row in enumerate(reader, start=1):
        email = col(row, EMAIL_COLUMN)
        taiga_id = col(row, TAIGA_ID_COLUMN)
        raw_value = col(row, VALUE_COLUMN)
        note = col(row, NOTE_COLUMN)
        ident = email or (f"taiga:{taiga_id}" if taiga_id else "")

        if not raw_value:
            result.errors.append(RowError(offset, ident, "missing value"))
            continue
        try:
            value = Decimal(raw_value)
        except (InvalidOperation, ValueError):
            result.errors.append(RowError(offset, ident, f"value '{raw_value}' is not a number"))
            continue

        membership, err = _resolve_membership(email, taiga_id, by_email, by_taiga)
        if err:
            result.errors.append(RowError(offset, ident, err))
            continue
        valid_rows.append((membership, value, note))

    with transaction.atomic():
        batch = ImportBatch.objects.create(
            org=org,
            kind=ImportKind.OPENING_BALANCE,
            filename=filename[:500],
            created_by=created_by,
            notes=f"mode={mode}; {len(valid_rows)} valid, {len(result.errors)} rejected rows",
        )

        if mode == MODE_REPLACE:
            replaced = set()
            for membership, _value, _note in valid_rows:
                if membership.pk not in replaced:
                    OpeningBalance.objects.filter(org=org, membership=membership).delete()
                    replaced.add(membership.pk)

        for membership, value, note in valid_rows:
            if (
                mode == MODE_APPEND
                and OpeningBalance.objects.filter(
                    org=org, membership=membership, value=value, source_note=note
                ).exists()
            ):
                result.skipped += 1
                continue
            OpeningBalance.objects.create(
                org=org, membership=membership, value=value, source_note=note
            )
            result.created += 1

        batch.row_count = result.created
        batch.save(update_fields=["row_count"])

    result.batch = batch
    return result


# --- Export convenience wrappers (thin; the adapters in exporters.py do the work) ------ #
def export_pie_csv(org) -> str:
    """Generic CSV snapshot of the current pie (member, issued value, share, unit)."""
    return get_exporter("generic").to_csv(org)


def export_slicing_pie(org) -> str:
    """Slicing Pie-format contribution export (hours/rate/cash offset + multiplier config)."""
    return get_exporter("slicing_pie").to_csv(org)
