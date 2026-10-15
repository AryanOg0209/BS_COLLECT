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
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .ageing import Status, assess
from .importer import ImportReport
from .models import Invoice, Promise, customer_key
from .money import to_money

SCHEMA_VERSION = 3

_SCHEMA_V1 = """
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

_SCHEMA_V2 = """
CREATE TABLE followups (
    id INTEGER PRIMARY KEY,
    customer_key TEXT NOT NULL,
    customer TEXT NOT NULL,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('call','whatsapp','email','visit','other')),
    outcome TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE promises (
    id INTEGER PRIMARY KEY,
    customer_key TEXT NOT NULL,
    customer TEXT NOT NULL,
    followup_id INTEGER NOT NULL REFERENCES followups(id),
    promised_date TEXT NOT NULL,
    promised_paise INTEGER CHECK (promised_paise IS NULL OR promised_paise > 0),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','kept','broken','cancelled')),
    resolved_ts TEXT,
    resolution_note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_followups_customer ON followups(customer_key, ts);
CREATE INDEX idx_promises_open ON promises(status, promised_date);
CREATE TRIGGER followups_no_update BEFORE UPDATE ON followups
BEGIN SELECT RAISE(ABORT, 'followups is append-only'); END;
CREATE TRIGGER followups_no_delete BEFORE DELETE ON followups
BEGIN SELECT RAISE(ABORT, 'followups is append-only'); END;
CREATE TRIGGER promises_no_delete BEFORE DELETE ON promises
BEGIN SELECT RAISE(ABORT, 'promises cannot be deleted; cancel them instead'); END;
"""

_SCHEMA_V3 = """
CREATE TABLE snapshots (
    id INTEGER PRIMARY KEY,
    as_of TEXT NOT NULL,
    taken_ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    audit_high_water INTEGER NOT NULL,
    total_outstanding_paise INTEGER NOT NULL,
    overdue_paise INTEGER NOT NULL,
    over30_paise INTEGER NOT NULL,
    over60_paise INTEGER NOT NULL,
    over90_paise INTEGER NOT NULL,
    open_invoices INTEGER NOT NULL,
    overdue_invoices INTEGER NOT NULL,
    overdue_customers INTEGER NOT NULL,
    followups_7d INTEGER NOT NULL,
    open_promises INTEGER NOT NULL,
    broken_promises INTEGER NOT NULL,
    payments_observed_paise INTEGER NOT NULL
);
CREATE INDEX idx_snapshots_as_of ON snapshots(as_of, id);
CREATE TRIGGER snapshots_no_update BEFORE UPDATE ON snapshots
BEGIN SELECT RAISE(ABORT, 'snapshots is append-only'); END;
CREATE TRIGGER snapshots_no_delete BEFORE DELETE ON snapshots
BEGIN SELECT RAISE(ABORT, 'snapshots is append-only'); END;
"""

_MIGRATIONS = {1: _SCHEMA_V1, 2: _SCHEMA_V2, 3: _SCHEMA_V3}


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


CHANNELS = ("call", "whatsapp", "email", "visit", "other")


@dataclass(frozen=True)
class FollowUp:
    id: int
    customer: str
    ts: str
    actor: str
    channel: str
    outcome: str
    note: str


@dataclass(frozen=True)
class Snapshot:
    """A point-in-time set of receivables measures. Descriptive only: it records what the data showed."""

    id: int
    as_of: date
    taken_ts: str
    actor: str
    total_outstanding: Decimal
    overdue: Decimal
    over30: Decimal
    over60: Decimal
    over90: Decimal
    open_invoices: int
    overdue_invoices: int
    overdue_customers: int
    followups_7d: int
    open_promises: int
    broken_promises: int
    payments_observed: Decimal  # increases in recorded paid amounts since the previous snapshot


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
        if version > SCHEMA_VERSION:
            raise StoreError(f"database schema v{version} is not supported (this build supports up to v{SCHEMA_VERSION})")
        for target in range(version + 1, SCHEMA_VERSION + 1):
            with self._db:  # each step is atomic; a failed step leaves the previous version intact
                self._db.executescript("BEGIN;" + _MIGRATIONS[target])
                self._db.execute(f"PRAGMA user_version = {target}")

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

    def _audit(self, ts, actor, action, entity_id, before, after, reason, entity="invoice") -> None:
        self._db.execute(
            "INSERT INTO audit_log (ts, actor, action, entity, entity_id, before_json, after_json, reason) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (ts, actor, action, entity, entity_id,
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

    # ---- follow-ups and promises ------------------------------------------------

    def log_followup(
        self,
        customer: str,
        channel: str,
        outcome: str,
        actor: str,
        note: str = "",
        promise_date: date | None = None,
        promise_amount: Decimal | None = None,
    ) -> tuple[int, int | None]:
        """Record a contact attempt; optionally record the promise made during it.

        Returns (followup_id, promise_id or None). Both rows are written atomically.
        """
        if channel not in CHANNELS:
            raise StoreError(f"channel must be one of {', '.join(CHANNELS)}")
        if not outcome.strip():
            raise StoreError("outcome is required (what happened on the contact?)")
        key = customer_key(customer)
        known = self._db.execute(
            "SELECT customer FROM invoices WHERE customer_key = ? LIMIT 1", (key,)
        ).fetchone()
        if known is None:
            raise StoreError(f"unknown customer {customer!r}: import their invoices first")
        now = self._now()
        if promise_date is None and promise_amount is not None:
            raise StoreError("promise_amount given without promise_date")
        if promise_date is not None and promise_date < now.date():
            raise StoreError(f"promise_date {promise_date} is in the past")
        paise = None if promise_amount is None else to_paise(promise_amount)
        if paise is not None and paise <= 0:
            raise StoreError("promise_amount must be positive")

        ts = now.isoformat(timespec="seconds")
        with self._db:
            followup_id = self._db.execute(
                "INSERT INTO followups (customer_key, customer, ts, actor, channel, outcome, note) "
                "VALUES (?,?,?,?,?,?,?)",
                (key, known["customer"], ts, actor, channel, outcome.strip(), note.strip()),
            ).lastrowid
            self._audit(ts, actor, "followup.logged", followup_id, None,
                        {"customer": known["customer"], "channel": channel, "outcome": outcome.strip()},
                        note.strip(), entity="followup")
            promise_id = None
            if promise_date is not None:
                promise_id = self._db.execute(
                    "INSERT INTO promises (customer_key, customer, followup_id, promised_date, promised_paise) "
                    "VALUES (?,?,?,?,?)",
                    (key, known["customer"], followup_id, promise_date.isoformat(), paise),
                ).lastrowid
                self._audit(ts, actor, "promise.created", promise_id, None,
                            {"customer": known["customer"], "promised_date": promise_date.isoformat(), "promised_paise": paise},
                            f"followup #{followup_id}", entity="promise")
        return followup_id, promise_id

    def resolve_promise(self, promise_id: int, status: str, actor: str, note: str = "") -> None:
        """Close an open promise as kept / broken / cancelled. Closed promises stay closed."""
        if status not in ("kept", "broken", "cancelled"):
            raise StoreError("status must be kept, broken or cancelled")
        row = self._db.execute("SELECT status FROM promises WHERE id = ?", (promise_id,)).fetchone()
        if row is None:
            raise StoreError(f"no promise #{promise_id}")
        if row["status"] != "open":
            raise StoreError(f"promise #{promise_id} is already {row['status']}")
        ts = self._now().isoformat(timespec="seconds")
        with self._db:
            self._db.execute(
                "UPDATE promises SET status = ?, resolved_ts = ?, resolution_note = ? WHERE id = ?",
                (status, ts, note.strip(), promise_id),
            )
            self._audit(ts, actor, f"promise.{status}", promise_id, {"status": "open"}, {"status": status},
                        note.strip(), entity="promise")

    def open_promises(self) -> list[Promise]:
        rows = self._db.execute(
            "SELECT * FROM promises WHERE status = 'open' ORDER BY promised_date, id"
        ).fetchall()
        return [
            Promise(
                r["id"], r["customer"], r["customer_key"], date.fromisoformat(r["promised_date"]),
                None if r["promised_paise"] is None else from_paise(r["promised_paise"]), r["status"],
            )
            for r in rows
        ]

    def last_contacts(self) -> dict[str, date]:
        """Date of the most recent follow-up per customer key."""
        rows = self._db.execute("SELECT customer_key, MAX(ts) AS ts FROM followups GROUP BY customer_key").fetchall()
        return {r["customer_key"]: datetime.fromisoformat(r["ts"]).date() for r in rows}

    def followups(self, customer: str | None = None) -> list[FollowUp]:
        sql, args = "SELECT * FROM followups", ()
        if customer is not None:
            sql, args = sql + " WHERE customer_key = ?", (customer_key(customer),)
        rows = self._db.execute(sql + " ORDER BY id", args).fetchall()
        return [FollowUp(r["id"], r["customer"], r["ts"], r["actor"], r["channel"], r["outcome"], r["note"]) for r in rows]

    # ---- snapshots --------------------------------------------------------------

    def take_snapshot(self, as_of: date, actor: str) -> Snapshot:
        """Record today's measures. Snapshots are immutable; take another to supersede one.

        payments_observed is the sum of *increases* in recorded paid amounts that arrived through
        imports since the previous snapshot (read from the audit trail). It is not an estimate:
        invoices that merely vanish from an export are not counted, and the first snapshot is a
        baseline with 0.
        """
        results = [assess(i, as_of) for i in self.invoices()]
        zero = Decimal("0.00")
        open_ = [r for r in results if r.status not in (Status.PAID, Status.OVERPAID)]
        overdue = [r for r in open_ if r.status is Status.OVERDUE]

        def total(rows):
            return sum((r.outstanding for r in rows), zero)

        window_start = as_of - timedelta(days=6)
        followups_7d = sum(1 for f in self.followups() if window_start <= datetime.fromisoformat(f.ts).date() <= as_of)
        promises = self.open_promises()

        high_water = self._db.execute("SELECT COALESCE(MAX(id), 0) FROM audit_log").fetchone()[0]
        prev = self._db.execute("SELECT audit_high_water FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        observed = self._payments_between(prev["audit_high_water"], high_water) if prev else zero

        ts = self._now().isoformat(timespec="seconds")
        with self._db:
            snapshot_id = self._db.execute(
                "INSERT INTO snapshots (as_of, taken_ts, actor, audit_high_water, total_outstanding_paise, "
                "overdue_paise, over30_paise, over60_paise, over90_paise, open_invoices, overdue_invoices, "
                "overdue_customers, followups_7d, open_promises, broken_promises, payments_observed_paise) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (as_of.isoformat(), ts, actor, high_water, to_paise(total(open_)), to_paise(total(overdue)),
                 to_paise(total([r for r in overdue if r.days_overdue > 30])),
                 to_paise(total([r for r in overdue if r.days_overdue > 60])),
                 to_paise(total([r for r in overdue if r.days_overdue > 90])),
                 len(open_), len(overdue), len({customer_key(r.invoice.customer) for r in overdue}),
                 followups_7d, len(promises), sum(1 for p in promises if p.promised_date < as_of),
                 to_paise(observed)),
            ).lastrowid
        return next(s for s in self.snapshots() if s.id == snapshot_id)

    def _payments_between(self, after_id: int, up_to_id: int) -> Decimal:
        rows = self._db.execute(
            "SELECT before_json, after_json FROM audit_log WHERE action = 'invoice.updated' AND id > ? AND id <= ?",
            (after_id, up_to_id),
        ).fetchall()
        paise = 0
        for r in rows:
            delta = json.loads(r["after_json"])["paid_paise"] - json.loads(r["before_json"])["paid_paise"]
            if delta > 0:
                paise += delta
        return from_paise(paise)

    def snapshots(self) -> list[Snapshot]:
        """All snapshots, oldest first. If several share an as_of date, the latest taken is kept."""
        rows = self._db.execute("SELECT * FROM snapshots ORDER BY as_of, id").fetchall()
        latest: dict[str, sqlite3.Row] = {r["as_of"]: r for r in rows}
        return [
            Snapshot(
                r["id"], date.fromisoformat(r["as_of"]), r["taken_ts"], r["actor"],
                from_paise(r["total_outstanding_paise"]), from_paise(r["overdue_paise"]),
                from_paise(r["over30_paise"]), from_paise(r["over60_paise"]), from_paise(r["over90_paise"]),
                r["open_invoices"], r["overdue_invoices"], r["overdue_customers"], r["followups_7d"],
                r["open_promises"], r["broken_promises"], from_paise(r["payments_observed_paise"]),
            )
            for r in latest.values()
        ]

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
