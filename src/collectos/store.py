"""SQLite persistence with an append-only audit trail.

Design rules:
- Money is stored as integer paise: exact, no float anywhere in the database.
- Every source row (accepted, duplicate or rejected) is kept verbatim in source_rows.
- Re-importing never deletes. Changed invoices are updated *and* audited with before/after.
- audit_log and source_rows are append-only, enforced by triggers, not by convention.
"""

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from .importer import ImportReport
from .models import Invoice
from .money import to_money

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE imports (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    actor TEXT NOT NULL
);
CREATE TABLE source_rows (
    id INTEGER PRIMARY KEY,
    import_id INTEGER NOT NULL REFERENCES imports(id),
    row_no INTEGER NOT NULL,
    disposition TEXT NOT NULL CHECK (disposition IN ('accepted','duplicate','rejected')),
    note TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL
);
CREATE TABLE invoices (
    id INTEGER PRIMARY KEY,
    invoice_no TEXT NOT NULL,
    customer TEXT NOT NULL,
    customer_key TEXT NOT NULL,
    invoice_date TEXT NOT NULL,
    due_date TEXT,
    amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
    paid_paise INTEGER NOT NULL CHECK (paid_paise >= 0),
    first_import_id INTEGER NOT NULL REFERENCES imports(id),
    last_import_id INTEGER NOT NULL REFERENCES imports(id),
    UNIQUE (invoice_no, customer_key)
);
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id INTEGER,
    before_json TEXT,
    after_json TEXT,
    reason TEXT NOT NULL DEFAULT ''
);
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER source_rows_no_update BEFORE UPDATE ON source_rows
BEGIN SELECT RAISE(ABORT, 'source_rows is append-only'); END;
CREATE TRIGGER source_rows_no_delete BEFORE DELETE ON source_rows
BEGIN SELECT RAISE(ABORT, 'source_rows is append-only'); END;
"""


class StoreError(RuntimeError):
    pass


@dataclass
class ApplyResult:
    import_id: int
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    missing_from_import: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AuditEntry:
    id: int
    ts: str
    actor: str
    action: str
    entity: str
    entity_id: int | None
    before: dict | None
    after: dict | None
    reason: str


def customer_key(name: str) -> str:
    return " ".join(name.split()).casefold()


def to_paise(amount: Decimal) -> int:
    return int(to_money(amount).scaleb(2))


def from_paise(paise: int) -> Decimal:
    return to_money(Decimal(paise).scaleb(-2))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Store:
    def __init__(self, path: str | Path = ":memory:", now: Callable[[], datetime] = _utc_now):
        self._now = now
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _migrate(self) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            with self._db:
                self._db.executescript(_SCHEMA)
                self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        elif version != SCHEMA_VERSION:
            raise StoreError(f"database schema v{version} is not supported (expected v{SCHEMA_VERSION})")

    # ---- writes -----------------------------------------------------------------

    def apply_import(self, report: ImportReport, actor: str, allow_errors: bool = False) -> ApplyResult:
        """Apply a validated import in a single transaction (all or nothing).

        Refuses a report that still has errors unless the caller explicitly accepts
        that those rows are being left out (allow_errors=True); they are still stored
        as 'rejected' source rows.
        """
        if report.errors and not allow_errors:
            raise StoreError(
                f"import has {len(report.errors)} error row(s); fix them or pass allow_errors=True"
            )
        ts = self._now().isoformat(timespec="seconds")
        with self._db:  # one transaction
            import_id = self._db.execute(
                "INSERT INTO imports (source, imported_at, actor) VALUES (?, ?, ?)",
                (report.source, ts, actor),
            ).lastrowid
            result = ApplyResult(import_id=import_id)
            filled = set(report.filled_due_dates)
            seen_ids: set[int] = set()

            for item in report.invoices:
                self._store_row(import_id, item.row_no, "accepted", "", item.raw)
                reason = f"import #{import_id} row {item.row_no}"
                if item.invoice.invoice_no in filled:
                    reason += "; due date filled from default credit days"
                row_id = self._upsert_invoice(item.invoice, import_id, actor, ts, reason, result)
                seen_ids.add(row_id)
            for dup in report.duplicates:
                self._store_row(import_id, dup.row_no, "duplicate", dup.message, dup.raw)
            for err in report.errors:
                self._store_row(import_id, err.row_no, "rejected", err.message, err.raw)

            result.missing_from_import = self._missing(seen_ids)
        return result

    def _store_row(self, import_id: int, row_no: int, disposition: str, note: str, raw: dict) -> None:
        self._db.execute(
            "INSERT INTO source_rows (import_id, row_no, disposition, note, raw_json) VALUES (?,?,?,?,?)",
            (import_id, row_no, disposition, note, json.dumps(raw, default=str, ensure_ascii=False)),
        )

    def _upsert_invoice(self, inv: Invoice, import_id: int, actor: str, ts: str, reason: str, result: ApplyResult) -> int:
        key = customer_key(inv.customer)
        existing = self._db.execute(
            "SELECT * FROM invoices WHERE invoice_no = ? AND customer_key = ?", (inv.invoice_no, key)
        ).fetchone()
        values = _invoice_values(inv)

        if existing is None:
            row_id = self._db.execute(
                "INSERT INTO invoices (invoice_no, customer, customer_key, invoice_date, due_date, "
                "amount_paise, paid_paise, first_import_id, last_import_id) VALUES (?,?,?,?,?,?,?,?,?)",
                (inv.invoice_no, inv.customer, key, values["invoice_date"], values["due_date"],
                 values["amount_paise"], values["paid_paise"], import_id, import_id),
            ).lastrowid
            self._audit(ts, actor, "invoice.created", row_id, None, {"invoice_no": inv.invoice_no, "customer": inv.customer, **values}, reason)
            result.inserted += 1
            return row_id

        before = {k: existing[k] for k in values}
        if before == values:
            self._db.execute("UPDATE invoices SET last_import_id = ? WHERE id = ?", (import_id, existing["id"]))
            result.unchanged += 1
            return existing["id"]

        self._db.execute(
            "UPDATE invoices SET customer = ?, invoice_date = ?, due_date = ?, amount_paise = ?, "
            "paid_paise = ?, last_import_id = ? WHERE id = ?",
            (inv.customer, values["invoice_date"], values["due_date"], values["amount_paise"],
             values["paid_paise"], import_id, existing["id"]),
        )
        self._audit(ts, actor, "invoice.updated", existing["id"], before, values, reason)
        if values["paid_paise"] < before["paid_paise"]:
            result.warnings.append(
                f"{inv.invoice_no} ({inv.customer}): paid amount decreased "
                f"{from_paise(before['paid_paise'])} -> {from_paise(values['paid_paise'])}; possible reversal"
            )
        result.updated += 1
        return existing["id"]

    def _missing(self, seen_ids: set[int]) -> list[str]:
        """Open invoices known to the store but absent from this import. Never auto-closed."""
        rows = self._db.execute("SELECT id, invoice_no, customer FROM invoices ORDER BY id").fetchall()
        return [f"{r['invoice_no']} ({r['customer']})" for r in rows if r["id"] not in seen_ids]

    def _audit(self, ts, actor, action, entity_id, before, after, reason) -> None:
        self._db.execute(
            "INSERT INTO audit_log (ts, actor, action, entity, entity_id, before_json, after_json, reason) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (ts, actor, action, "invoice", entity_id,
             None if before is None else json.dumps(before, sort_keys=True),
             None if after is None else json.dumps(after, sort_keys=True), reason),
        )

    # ---- reads ------------------------------------------------------------------

    def invoices(self) -> list[Invoice]:
        rows = self._db.execute("SELECT * FROM invoices ORDER BY id").fetchall()
        return [
            Invoice(
                r["invoice_no"], r["customer"], date.fromisoformat(r["invoice_date"]),
                None if r["due_date"] is None else date.fromisoformat(r["due_date"]),
                from_paise(r["amount_paise"]), from_paise(r["paid_paise"]),
            )
            for r in rows
        ]

    def audit_entries(self) -> list[AuditEntry]:
        rows = self._db.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
        return [
            AuditEntry(
                r["id"], r["ts"], r["actor"], r["action"], r["entity"], r["entity_id"],
                None if r["before_json"] is None else json.loads(r["before_json"]),
                None if r["after_json"] is None else json.loads(r["after_json"]),
                r["reason"],
            )
            for r in rows
        ]

    def source_rows(self, import_id: int) -> list[sqlite3.Row]:
        return self._db.execute(
            "SELECT row_no, disposition, note, raw_json FROM source_rows WHERE import_id = ? ORDER BY id",
            (import_id,),
        ).fetchall()

    # ---- operations -------------------------------------------------------------

    def backup(self, dest: str | Path) -> None:
        """Consistent online copy using SQLite's backup API."""
        target = sqlite3.connect(str(dest))
        try:
            self._db.backup(target)
        finally:
            target.close()


def _invoice_values(inv: Invoice) -> dict[str, object]:
    return {
        "invoice_date": inv.invoice_date.isoformat(),
        "due_date": None if inv.due_date is None else inv.due_date.isoformat(),
        "amount_paise": to_paise(inv.amount),
        "paid_paise": to_paise(inv.paid),
    }
