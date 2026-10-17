"""Registers that sit beside the invoice ledger: post-dated cheques and TDS deducted by customers.

Neither register changes an invoice balance. A cheque in hand is not payment until it clears and the
invoice is updated (by the next import, or by a person confirming it as paid), and TDS is recorded so the
owner can match it against Form 26AS at quarter end. Every change is written to the audit log with
before/after values; rows are never deleted (a cheque is marked returned, a TDS entry is voided).
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .errors import StoreError
from .models import customer_key
from .money import from_paise, to_money, to_paise
from .taxcal import fy_quarter, valid_fy

SCHEMA_V6 = """
ALTER TABLE customers ADD COLUMN group_name TEXT NOT NULL DEFAULT '';
CREATE TABLE cheques (
    id INTEGER PRIMARY KEY,
    customer_key TEXT NOT NULL,
    customer TEXT NOT NULL,
    cheque_no TEXT NOT NULL,
    bank TEXT NOT NULL DEFAULT '',
    amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
    cheque_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'received' CHECK (status IN ('received', 'deposited', 'cleared', 'bounced', 'returned')),
    invoice_nos TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_ts TEXT NOT NULL,
    created_by TEXT NOT NULL,
    updated_ts TEXT NOT NULL
);
CREATE INDEX idx_cheques_customer ON cheques(customer_key);
CREATE INDEX idx_cheques_status ON cheques(status);
CREATE TRIGGER cheques_no_delete BEFORE DELETE ON cheques
BEGIN SELECT RAISE(ABORT, 'cheques are never deleted; mark one returned'); END;
CREATE TABLE tds_entries (
    id INTEGER PRIMARY KEY,
    customer_key TEXT NOT NULL,
    customer TEXT NOT NULL,
    invoice_no TEXT NOT NULL DEFAULT '',
    deducted_on TEXT NOT NULL,
    fy TEXT NOT NULL,
    quarter INTEGER NOT NULL CHECK (quarter BETWEEN 1 AND 4),
    tds_paise INTEGER NOT NULL CHECK (tds_paise > 0),
    gross_paise INTEGER CHECK (gross_paise IS NULL OR gross_paise > 0),
    in_26as INTEGER NOT NULL DEFAULT 0 CHECK (in_26as IN (0, 1)),
    voided INTEGER NOT NULL DEFAULT 0 CHECK (voided IN (0, 1)),
    note TEXT NOT NULL DEFAULT '',
    created_ts TEXT NOT NULL,
    created_by TEXT NOT NULL,
    updated_ts TEXT NOT NULL
);
CREATE INDEX idx_tds_fy ON tds_entries(fy, quarter);
CREATE TRIGGER tds_no_delete BEFORE DELETE ON tds_entries
BEGIN SELECT RAISE(ABORT, 'TDS entries are never deleted; void one'); END;
"""

CHEQUE_STATUSES = ("received", "deposited", "cleared", "bounced", "returned")
# what a cheque can become next; cleared and returned are final
CHEQUE_NEXT = {"received": ("deposited", "returned"), "deposited": ("cleared", "bounced"), "bounced": ("deposited", "returned"), "cleared": (), "returned": ()}
OPEN_CHEQUE = ("received", "deposited", "bounced")


@dataclass(frozen=True)
class Cheque:
    id: int
    customer: str
    customer_key: str
    cheque_no: str
    bank: str
    amount: Decimal
    cheque_date: date
    status: str
    invoice_nos: str
    note: str
    created_ts: str
    updated_ts: str


@dataclass(frozen=True)
class TdsEntry:
    id: int
    customer: str
    customer_key: str
    invoice_no: str
    deducted_on: date
    fy: str
    quarter: int
    tds: Decimal
    gross: Decimal | None
    in_26as: bool
    voided: bool
    note: str
    created_ts: str


def _short(text: str, limit: int, what: str) -> str:
    text = " ".join(str(text or "").split())
    if len(text) > limit:
        raise StoreError(f"{what} is too long (at most {limit} characters)")
    return text


class RegistersMixin:
    def _customer_name(self, name: str) -> str:
        row = self._db.execute("SELECT customer FROM invoices WHERE customer_key = ? LIMIT 1", (customer_key(name),)).fetchone()
        if row is None:
            raise StoreError(f"unknown customer {name!r}")
        return row["customer"]

    def last_invoice_change_ts(self) -> str | None:
        """When the invoice data last changed (an import or a confirmed settlement), or None if it never has."""
        row = self._db.execute("SELECT MAX(ts) AS ts FROM audit_log WHERE entity = 'invoice' AND action IN ('invoice.created', 'invoice.updated', 'invoice.settled')").fetchone()
        return row["ts"]

    # ---- post-dated cheques ------------------------------------------------------------------

    @staticmethod
    def _cheque(r) -> Cheque:
        return Cheque(r["id"], r["customer"], r["customer_key"], r["cheque_no"], r["bank"], from_paise(r["amount_paise"]),
                      date.fromisoformat(r["cheque_date"]), r["status"], r["invoice_nos"], r["note"], r["created_ts"], r["updated_ts"])

    def add_cheque(self, customer: str, cheque_no: str, amount, cheque_date: date, actor: str, bank: str = "", invoice_nos: str = "", note: str = "") -> int:
        display = self._customer_name(customer)
        cheque_no = _short(cheque_no, 40, "cheque number")
        if not cheque_no:
            raise StoreError("cheque number is required")
        bank, invoice_nos, note = _short(bank, 60, "bank"), _short(invoice_nos, 200, "invoice numbers"), _short(note, 500, "note")
        try:
            amount = to_money(amount)
        except ValueError as exc:
            raise StoreError(str(exc)) from None
        if amount <= 0:
            raise StoreError("cheque amount must be more than zero")
        key = customer_key(customer)
        if self._db.execute("SELECT 1 FROM cheques WHERE customer_key = ? AND cheque_no = ? AND bank = ? AND status != 'returned'", (key, cheque_no, bank)).fetchone():
            raise StoreError("that cheque number is already in the register for this customer and bank")
        ts = self._now().isoformat(timespec="seconds")
        with self._db:
            cid = self._db.execute(
                "INSERT INTO cheques (customer_key, customer, cheque_no, bank, amount_paise, cheque_date, status, invoice_nos, note, created_ts, created_by, updated_ts) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, display, cheque_no, bank, to_paise(amount), cheque_date.isoformat(), "received", invoice_nos, note, ts, actor, ts)).lastrowid
            self._audit(ts, actor, "cheque.added", cid, None, {"customer": display, "cheque_no": cheque_no, "bank": bank, "amount": str(amount),
                                                                "cheque_date": cheque_date.isoformat(), "invoice_nos": invoice_nos}, "", entity="cheque")
        return cid

    def set_cheque_status(self, cheque_id: int, status: str, actor: str, note: str = "") -> Cheque:
        row = self._db.execute("SELECT * FROM cheques WHERE id = ?", (cheque_id,)).fetchone()
        if row is None:
            raise StoreError(f"unknown cheque {cheque_id}")
        if status not in CHEQUE_STATUSES:
            raise StoreError(f"status must be one of: {', '.join(CHEQUE_STATUSES)}")
        if status not in CHEQUE_NEXT[row["status"]]:
            allowed = ", ".join(CHEQUE_NEXT[row["status"]]) or "nothing, it is final"
            raise StoreError(f"a {row['status']} cheque can become: {allowed}")
        note = _short(note, 500, "note")
        ts = self._now().isoformat(timespec="seconds")
        with self._db:
            self._db.execute("UPDATE cheques SET status = ?, updated_ts = ?, note = CASE WHEN ? != '' THEN ? ELSE note END WHERE id = ?", (status, ts, note, note, cheque_id))
            self._audit(ts, actor, "cheque.status", cheque_id, {"status": row["status"], "customer": row["customer"], "cheque_no": row["cheque_no"]},
                        {"status": status, "customer": row["customer"], "cheque_no": row["cheque_no"]}, note, entity="cheque")
        return self._cheque(self._db.execute("SELECT * FROM cheques WHERE id = ?", (cheque_id,)).fetchone())

    def cheques(self, status: str | None = None, customer: str | None = None) -> list[Cheque]:
        sql, args = "SELECT * FROM cheques", []
        where = []
        if status:
            where.append("status = ?"); args.append(status)
        if customer:
            where.append("customer_key = ?"); args.append(customer_key(customer))
        if where:
            sql += " WHERE " + " AND ".join(where)
        return [self._cheque(r) for r in self._db.execute(sql + " ORDER BY cheque_date, id", args).fetchall()]

    # ---- TDS deducted by customers -----------------------------------------------------------

    @staticmethod
    def _tds(r) -> TdsEntry:
        return TdsEntry(r["id"], r["customer"], r["customer_key"], r["invoice_no"], date.fromisoformat(r["deducted_on"]), r["fy"], r["quarter"],
                        from_paise(r["tds_paise"]), None if r["gross_paise"] is None else from_paise(r["gross_paise"]), bool(r["in_26as"]),
                        bool(r["voided"]), r["note"], r["created_ts"])

    def add_tds(self, customer: str, deducted_on: date, tds, actor: str, invoice_no: str = "", gross=None, note: str = "") -> int:
        display = self._customer_name(customer)
        invoice_no, note = _short(invoice_no, 60, "invoice number"), _short(note, 500, "note")
        try:
            tds = to_money(tds)
            gross = None if gross in (None, "") else to_money(gross)
        except ValueError as exc:
            raise StoreError(str(exc)) from None
        if tds <= 0:
            raise StoreError("TDS amount must be more than zero")
        if gross is not None and gross <= tds:
            raise StoreError("the gross amount must be more than the TDS")
        fy, quarter = fy_quarter(deducted_on)
        ts = self._now().isoformat(timespec="seconds")
        with self._db:
            tid = self._db.execute(
                "INSERT INTO tds_entries (customer_key, customer, invoice_no, deducted_on, fy, quarter, tds_paise, gross_paise, note, created_ts, created_by, updated_ts) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (customer_key(customer), display, invoice_no, deducted_on.isoformat(), fy, quarter, to_paise(tds), None if gross is None else to_paise(gross),
                 note, ts, actor, ts)).lastrowid
            self._audit(ts, actor, "tds.added", tid, None, {"customer": display, "invoice_no": invoice_no, "tds": str(tds), "fy": fy, "quarter": quarter}, "", entity="tds")
        return tid

    def set_tds_26as(self, tds_id: int, seen: bool, actor: str) -> None:
        row = self._db.execute("SELECT * FROM tds_entries WHERE id = ?", (tds_id,)).fetchone()
        if row is None:
            raise StoreError(f"unknown TDS entry {tds_id}")
        if row["voided"]:
            raise StoreError("that entry was voided")
        if bool(row["in_26as"]) == seen:
            return
        ts = self._now().isoformat(timespec="seconds")
        with self._db:
            self._db.execute("UPDATE tds_entries SET in_26as = ?, updated_ts = ? WHERE id = ?", (int(seen), ts, tds_id))
            self._audit(ts, actor, "tds.26as", tds_id, {"in_26as": bool(row["in_26as"]), "customer": row["customer"]}, {"in_26as": seen, "customer": row["customer"]}, "", entity="tds")

    def void_tds(self, tds_id: int, actor: str, note: str = "") -> None:
        row = self._db.execute("SELECT * FROM tds_entries WHERE id = ?", (tds_id,)).fetchone()
        if row is None:
            raise StoreError(f"unknown TDS entry {tds_id}")
        if row["voided"]:
            return
        note = _short(note, 500, "note")
        ts = self._now().isoformat(timespec="seconds")
        with self._db:
            self._db.execute("UPDATE tds_entries SET voided = 1, updated_ts = ? WHERE id = ?", (ts, tds_id))
            self._audit(ts, actor, "tds.voided", tds_id, {"voided": False, "customer": row["customer"], "tds": from_paise(row["tds_paise"]).__str__()}, {"voided": True}, note, entity="tds")

    def tds_entries(self, fy: str | None = None, include_void: bool = False) -> list[TdsEntry]:
        if fy is not None and not valid_fy(fy):
            raise StoreError("financial year must look like 2026-27")
        sql, args, where = "SELECT * FROM tds_entries", [], []
        if fy:
            where.append("fy = ?"); args.append(fy)
        if not include_void:
            where.append("voided = 0")
        if where:
            sql += " WHERE " + " AND ".join(where)
        return [self._tds(r) for r in self._db.execute(sql + " ORDER BY deducted_on, id", args).fetchall()]
