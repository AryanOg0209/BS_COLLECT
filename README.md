# CollectOS

Local-first receivables toolkit for Indian B2B SMEs. Takes the outstanding-invoice export a
business already has (TallyPrime *Bills Receivable*, Excel, CSV) and produces a prioritised
follow-up list with reasons, a promise-to-pay log, reminder drafts and an owner report.

Codename only; not a registered brand. Status: early build, **not yet validated with real customers**.

## Principles
- Money is `decimal.Decimal` (integer paise in the database), never `float`. Source rows are preserved; changes are audited.
- No cloud, no paid APIs, nothing is ever sent automatically. Reminder drafts are text for a person to review.
- **Never commit real customer data.** Only synthetic data belongs in this repo (`.gitignore` blocks databases and spreadsheets).
- Not suitable for live customer financial data until the security checklist in the strategy plan is met; use synthetic or anonymised data for demos.

## Quickstart
```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                                  # 188 tests

# Build the whole 10-minute demo kit from synthetic data
.venv/bin/collectos demo ./demo-kit
```
`demo-kit/` gets a synthetic invoice CSV, `owner_report.xlsx`, `reminder_drafts.txt` and a demo database.

With a real export (keep it outside the repo or under the git-ignored `data/`):
```bash
collectos import export.xlsx --db data/acme.db            # validates, reports bad rows, saves nothing if errors
collectos queue --db data/acme.db                          # who to chase today, and why
collectos log "Acme Traders" --channel call --outcome "Promised part payment" \
        --promise-date 20-10-2026 --promise-amount 50000 --db data/acme.db
collectos draft "Acme Traders" --sender "Rohit" --company "Sharma Steel" --db data/acme.db
collectos resolve 1 kept --note "NEFT received" --db data/acme.db
collectos snapshot --db data/acme.db                       # day 1 = baseline; repeat weekly after each import
collectos trend --db data/acme.db                          # snapshots side by side
collectos report owner.xlsx --company "Sharma Steel" --db data/acme.db   # adds a Trend sheet once snapshots exist
```

**Measured outcomes.** A snapshot records overdue totals, ageing, follow-ups, promises and *payments seen in
data* (increases in recorded paid amounts that arrived via imports since the previous snapshot, read from the
audit log). These numbers are descriptive: they do not prove that follow-up work caused any change, and
invoices that merely disappear from an export are not counted as payments.

## Layers
| # | Layer | Module(s) | Status |
|---|-------|-----------|--------|
| 0 | Scaffold, data-safety `.gitignore` | | done |
| 1 | Decimal money, invoice model, ageing rules (pure, no I/O) | `money`, `models`, `ageing` | done |
| 2 | Import pipeline: CSV/XLSX, header detection, issue report | `importer`, `readers`, `dates` | done |
| 3 | SQLite store, append-only audit log, atomic imports | `store` | done |
| 4 | Work queue with explained scoring; follow-ups and promises | `workqueue`, `store` | done |
| 5 | Reminder drafts and Excel owner report | `reminders`, `report` | done |
| 6 | CLI and deterministic synthetic data | `cli`, `synthetic` | done |
| 7 | Baseline + weekly snapshots, Trend sheet, 5-week synthetic demo history | `store`, `report`, `synthetic` | done |

## Known limitations (read before relying on it)
- **Tally column names are assumptions.** The importer accepts common aliases (Ref. No., Party's Name, Pending Amount, Due on, ...) but has not yet been run against a real TallyPrime export. Do that first.
- If only a *pending/outstanding* column exists, partial payments cannot be detected (the importer warns).
- Invoices absent from a later import are reported but never auto-closed.
- Queue scoring tiers saturate (a customer over ₹1 lakh and 90+ days scores the same as a bigger/older one); amount breaks ties. Tune `QueueRules` with real data.
- Dates read day-first; two-digit years follow Python's pivot.
- No bank/UTR reconciliation, no sending (email/WhatsApp), no multi-user access, no hosted mode. These are deliberately deferred until a customer needs them.
- Credit notes (negative invoices) are rejected for now.
- No encryption at rest beyond what the disk provides (use FileVault/BitLocker).

## Layout
```
src/collectos/   library + CLI          tests/   pytest suite (synthetic data only)
data/            git-ignored workspace  .venv/   local environment
```
