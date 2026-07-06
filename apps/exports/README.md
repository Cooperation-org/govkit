# apps.exports — import + export

This app moves equity **in** (opening-balance import) and **out** (CSV / Slicing Pie
export) of GovKit. Equity only ever leaves through an exporter (settled design decision
#2); starting a team with pre-existing equity is done through the importer (there is no
separate "demo data" path — the import *is* that feature).

The app owns one model, `ImportBatch` (an audit record of an import run). Opening balances
land in `orgs.OpeningBalance`; the pie/contribution figures are read from the frozen
`drops` + `orgs` models (a minimal local sum in `exporters.py`, so this app never imports
the concurrently-built `apps.pie`).

## Opening-balance import

### CSV schema

| Column          | Required | Meaning                                                       |
|-----------------|----------|---------------------------------------------------------------|
| `value`         | yes      | Opening equity for the member, in the org's unit (a decimal). |
| `member_email`  | one of\* | Member identifier — matched to `Membership.user.email`.       |
| `taiga_user_id` | one of\* | Member identifier — matched to `Membership.taiga_user_id`.    |
| `source_note`   | no       | Free-text provenance (e.g. "issued_cook export 2026-07").     |

\* Every row must carry **at least one** of `member_email` / `taiga_user_id`. If both are
present they must resolve to the **same** member, or the row is rejected. Identifiers are
mapped to a `Membership` **explicitly** — an identifier that matches no member is reported
as a bad row and **never** creates a member (identity mapping is explicit, never inferred).

Headers are case-insensitive and order-independent. A UTF-8 BOM is tolerated.

Example (`opening_balances.csv`):

```csv
member_email,value,source_note
member1@example.org,1200.00,issued_cook history
member2@example.org,850.50,issued_cook history
```

### Re-run semantics

`import_opening_balances(org, fileobj, created_by=None, mode="replace")`:

- **`replace`** (default) — for every member named in the file, their existing opening
  balances are removed and replaced by the file's rows. Re-running a corrected file
  converges to the same state (**idempotent**).
- **`append`** — add rows, skipping an exact `(member, value, source_note)` duplicate. Re-
  running the *same* file is a no-op.

Bad rows are reported (returned in `ImportResult.errors`), not created; valid rows still
import. A structural failure (unreadable CSV, missing `value` column) raises
`CsvImportError` and creates no batch. Every run that reaches the write step is recorded
as an `ImportBatch` (filename, row count, who, mode + reject count in `notes`) for audit.

### Producing the CSV from the legacy `issued_cook` table

GovKit does **not** read the legacy Taiga DB live (self-hosters have no DB access, and the
direct-DB script is the thing being replaced). Historical import is just another input: a
one-off documented CSV export from the old system, hand-carried in.

On the machine that has the legacy DB, dump the table with `psql \copy` (client-side, no
server file permissions needed). Map each legacy row to a member identifier your GovKit
memberships carry — e.g. the member's email or Taiga user id — in the SELECT:

```sql
-- Run against the legacy database; writes a local CSV in the import schema above.
\copy (
    SELECT
        member_email          AS member_email,   -- or taiga_user_id AS taiga_user_id
        SUM(value)            AS value,           -- collapse to one opening balance/member
        'issued_cook history' AS source_note
    FROM issued_cook
    GROUP BY member_email
    ORDER BY member_email
) TO 'opening_balances.csv' WITH (FORMAT csv, HEADER true);
```

Adjust the identifier column to whatever the legacy table stores (add a join to the legacy
user table if the balance is keyed by user id and you want emails). Then upload
`opening_balances.csv` on the org's Import / Export page (or POST it to the API below).

## Export

Exports are pluggable **`EquityExport`** adapters (`exporters.py`). Add a target (Fairmint,
etc.) by subclassing `EquityExport`, implementing `fieldnames()` + `rows(org)`, and adding
one entry to the `EXPORTERS` registry — no other code changes.

- **Generic CSV** (`generic`) — one row per member with equity: `member_email`,
  `taiga_user_id`, `issued_value`, `opening_balance`, `total_value`, `share`, `unit`.
  `total = Σ final_value` over drop lines in **approved** runs `+ Σ` opening balances.
- **Slicing Pie** (`slicing_pie`) — one row per *contribution* (each issued drop line, and
  each opening balance as a historical contribution), compatible with the Slicing Pie
  model (we are allies, not competitors). Surfaces `hours` / `rate` / `cash_offset` (hours
  and cash summed from the tracked tasks a line was computed from), the org's at-risk
  multipliers as config-driven columns (`at_risk_multiplier_noncash` / `_cash`), and
  `slices` = the line's `final_value` (the at-risk-adjusted value the drop engine already
  computed — a faithful re-export, not a re-derivation).

## API (API-first)

Mounted under an `o/<org_slug>/` prefix so `OrgContextMiddleware` resolves `request.org`
and enforces membership:

```
GET  /api/v1/exports/o/<org_slug>/batches/               list import batches (audit)
GET  /api/v1/exports/o/<org_slug>/batches/<pk>/          one batch
POST /api/v1/exports/o/<org_slug>/batches/import_csv/    upload a CSV (admin/steward; multipart 'file', optional 'mode')
GET  /api/v1/exports/o/<org_slug>/export/<format>/       download an export (generic | slicing_pie)
```

HTML pages: `…/o/<org_slug>/exports/` (import form + export links + history).
Import is admin/steward-gated; export is readable by any org member.
