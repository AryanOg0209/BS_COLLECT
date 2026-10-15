# CollectOS

Local-first receivables toolkit for Indian B2B SMEs. Takes the outstanding-invoice export a
business already has (TallyPrime *Bills Receivable*, Excel, CSV) and produces a prioritised
follow-up list, a promise-to-pay log, reminder drafts and an owner report.

Codename only; not a registered brand. Status: early build, validating with real customers.

## Principles
- Money is `decimal.Decimal`, never `float`. Source rows are preserved; corrections are append-only.
- No cloud, no paid APIs, no automated sending in v1.
- **Never commit real customer data.** Only synthetic fixtures belong in this repo (see `.gitignore`).

## Layers (built one at a time)
| # | Layer | Status |
|---|-------|--------|
| 0 | Scaffold, tooling | done |
| 1 | Money + invoice model + ageing rules (pure, no I/O) | done |
| 2 | Import pipeline (CSV/Excel/Tally-style export → validated invoices + issue report) | done |
| 3 | SQLite persistence + append-only audit log | done |
| 4 | Work queue (transparent priority rules + reasons) + follow-up / promise-to-pay log | done |
| 5 | Reminder drafts (text only, nothing sent) + owner report (Excel) | done |
| 6 | CLI + synthetic demo data | next |

## Develop
```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```
