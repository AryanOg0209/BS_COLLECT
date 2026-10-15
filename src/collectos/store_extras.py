"""Ledger features beyond the core import/follow-up/snapshot: customer master, disputes,
settings, settle-by-confirmation, promise reliability, and a read-only import diff.

Mixed into Store (it uses the store's connection, clock and audit writer). Everything that
changes data is audited with before/after values, and nothing is ever guessed: invoices that
vanish from an export are only closed when a person confirms they were paid.
"""

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from .contacts import normalise_email, normalise_phone
from .errors import StoreError
from .importer import ImportReport
from .models import Promise, customer_key
from .money import from_paise, to_money, to_paise

SCHEMA_V4 = """
CREATE TABLE customers (
    customer_key TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    contact_person TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    preferred_channel TEXT NOT NULL DEFAULT '' CHECK (preferred_channel IN ('', 'call', 'whatsapp', 'email', 'visit')),
    credit_limit_paise INTEGER CHECK (credit_limit_paise IS NULL OR credit_limit_paise > 0),
    do_not_contact INTEGER NOT NULL DEFAULT 0 CHECK (do_not_contact IN (0, 1)),
    notes TEXT NOT NULL DEFAULT '',
    updated_ts TEXT NOT NULL
);
CREATE TABLE disputes (
    id INTEGER PRIMARY KEY,
    invoice_no TEXT NOT NULL,
    customer_key TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('price', 'quantity', 'quality', 'delivery', 'tax', 'duplicate', 'other')),
    note TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    opened_ts TEXT NOT NULL,
    opened_by TEXT NOT NULL,
    resolved_ts TEXT,
    resolved_by TEXT,
    resolution_note TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX uq_open_dispute ON disputes(invoice_no, customer_key) WHERE status = 'open';
CREATE INDEX idx_disputes_customer ON disputes(customer_key, status);
CREATE TRIGGER disputes_no_delete BEFORE DELETE ON disputes
BEGIN SELECT RAISE(ABORT, 'disputes cannot be deleted; resolve them instead'); END;
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_ts TEXT NOT NULL
);
"""

SCHEMA_V5 = """
CREATE TABLE ai_usage (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    month TEXT NOT NULL,
    actor TEXT NOT NULL,
    feature TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_micro_usd INTEGER NOT NULL
);
CREATE INDEX idx_ai_usage_month ON ai_usage(month);
CREATE TRIGGER ai_usage_no_update BEFORE UPDATE ON ai_usage
BEGIN SELECT RAISE(ABORT, 'ai_usage is append-only'); END;
CREATE TRIGGER ai_usage_no_delete BEFORE DELETE ON ai_usage
BEGIN SELECT RAISE(ABORT, 'ai_usage is append-only'); END;
"""

DISPUTE_CATEGORIES = ("price", "quantity", "quality", "delivery", "tax", "duplicate", "other")
CHANNEL_PREFERENCES = ("", "call", "whatsapp", "email", "visit")
SETTING_DEFAULTS = {"msme_supplier": "0", "default_country_code": "91", "ai_assist": "0", "ai_budget_usd": "2.00", "ai_usd_inr": "90.00"}
_PAYMENT_ACTIONS = ("invoice.updated", "invoice.settled")
_UNSET = object()


@dataclass(frozen=True)
class CustomerProfile:
    key: str
    name: str
    contact_person: str = ""
    phone: str = ""
    email: str = ""
    preferred_channel: str = ""
    credit_limit: Decimal | None = None
    do_not_contact: bool = False
    notes: str = ""


@dataclass(frozen=True)
class Dispute:
    id: int
    invoice_no: str
    customer: str
    customer_key: str
    category: str
    note: str
    owner: str
    status: str
    opened_ts: str
    opened_by: str
    resolved_ts: str | None
    resolution_note: str
    outstanding: Decimal


@dataclass(frozen=True)
class PromiseRecord:
    id: int
    customer: str
    customer_key: str
    promised_date: date
    promised_amount: Decimal | None
    status: str
    created_ts: str
    resolved_ts: str | None
    resolution_note: str


@dataclass(frozen=True)
class Reliability:
    kept: int
    broken: int  # promises marked broken, plus open promises whose date has passed

    @property
    def total(self) -> int:
        return self.kept + self.broken


@dataclass(frozen=True)
class PromiseSuggestion:
    promise: PromiseRecord
    observed: Decimal
    kind: str  # "looks_kept" or "partial"


@dataclass(frozen=True)
class InvoiceChange:
    invoice_no: str
    customer: str
    paid_before: Decimal
    paid_after: Decimal
    amount_before: Decimal
    amount_after: Decimal
    due_before: date | None
    due_after: date | None

    @property
    def paid_delta(self) -> Decimal:
        return self.paid_after - self.paid_before


@dataclass
class ImportDiff:
    """What applying an import would do, computed without writing anything."""

    new: list[tuple[str, str, Decimal]] = field(default_factory=list)          # invoice_no, customer, amount
    changed: list[InvoiceChange] = field(default_factory=list)
    unchanged: int = 0
    missing: list[tuple[str, str, Decimal]] = field(default_factory=list)      # invoice_no, customer, outstanding
    payments_seen: Decimal = Decimal("0.00")
    reversals: list[InvoiceChange] = field(default_factory=list)


class ExtrasMixin:
    # ---- settings ---------------------------------------------------------------------

    def get_setting(self, key: str) -> str:
        if key not in SETTING_DEFAULTS:
            raise StoreError(f"unknown setting {key!r}")
        row = self._db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else SETTING_DEFAULTS[key]

    def set_setting(self, key: str, value: str, actor: str) -> None:
        value = str(value).strip()
        if key == "msme_supplier" and value not in ("0", "1"):
            raise StoreError("msme_supplier must be 0 or 1")
        if key == "default_country_code" and not (value.isdigit() and 1 <= len(value) <= 3):
            raise StoreError("default_country_code must be 1 to 3 digits")
        if key == "ai_assist" and value not in ("0", "1"):
            raise StoreError("ai_assist must be 0 or 1")
        if key in ("ai_budget_usd", "ai_usd_inr"):
            value = self._money_setting(key, value)
        before = self.get_setting(key)
        if before == value:
            return
        ts = self._now().isoformat(timespec="seconds")
        with self._db:
            self._db.execute("INSERT INTO settings (key, value, updated_ts) VALUES (?,?,?) "
                             "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_ts = excluded.updated_ts", (key, value, ts))
            self._audit(ts, actor, "setting.changed", None, {key: before}, {key: value}, "", entity="setting")

    @staticmethod
    def _money_setting(key: str, value: str) -> str:
        low, high = (Decimal("0"), Decimal("100")) if key == "ai_budget_usd" else (Decimal("1"), Decimal("500"))
        try:
            number = Decimal(value)
        except Exception:
            raise StoreError(f"{key} must be a number") from None
        if not number.is_finite() or not low <= number <= high:
            raise StoreError(f"{key} must be between {low} and {high}")
        return f"{number.quantize(Decimal('0.01')):.2f}"

    # ---- optional AI assist: what was sent, and what it cost ---------------------------------

    def record_ai_usage(self, feature: str, model: str, input_tokens: int, output_tokens: int, cost_micro_usd: int, actor: str, sent_text: str) -> int:
        """Append one AI call to the ledger and to the audit log (with exactly the text that left this computer)."""
        ts = self._now().isoformat(timespec="seconds")
        with self._db:
            row_id = self._db.execute(
                "INSERT INTO ai_usage (ts, month, actor, feature, model, input_tokens, output_tokens, cost_micro_usd) VALUES (?,?,?,?,?,?,?,?)",
                (ts, ts[:7], actor, feature, model, int(input_tokens), int(output_tokens), int(cost_micro_usd))).lastrowid
            self._audit(ts, actor, "ai.request", row_id, None,
                        {"feature": feature, "model": model, "input_tokens": int(input_tokens), "output_tokens": int(output_tokens),
                         "cost_usd": f"{cost_micro_usd / 1_000_000:.6f}", "sent": sent_text[:4000]}, "", entity="ai")
        return row_id

    def ai_month_summary(self, month: str | None = None) -> dict:
        month = month or self._now().isoformat(timespec="seconds")[:7]
        row = self._db.execute("SELECT COUNT(*) AS calls, COALESCE(SUM(cost_micro_usd), 0) AS cost, COALESCE(SUM(input_tokens), 0) AS tin, "
                               "COALESCE(SUM(output_tokens), 0) AS tout FROM ai_usage WHERE month = ?", (month,)).fetchone()
        return {"month": month, "calls": row["calls"], "cost_micro_usd": row["cost"], "input_tokens": row["tin"], "output_tokens": row["tout"]}

    # ---- customer master ---------------------------------------------------------------

    def _known_customer(self, name: str) -> str:
        row = self._db.execute("SELECT customer FROM invoices WHERE customer_key = ? LIMIT 1", (customer_key(name),)).fetchone()
        if row is None:
            raise StoreError(f"unknown customer {name!r}")
        return row["customer"]

    @staticmethod
    def _profile(row, fallback_key: str = "", fallback_name: str = "") -> CustomerProfile:
        if row is None:
            return CustomerProfile(fallback_key, fallback_name)
        return CustomerProfile(
            row["customer_key"], row["display_name"], row["contact_person"], row["phone"], row["email"], row["preferred_channel"],
            None if row["credit_limit_paise"] is None else from_paise(row["credit_limit_paise"]), bool(row["do_not_contact"]), row["notes"])

    def customer_profile(self, name: str) -> CustomerProfile:
        display = self._known_customer(name)
        row = self._db.execute("SELECT * FROM customers WHERE customer_key = ?", (customer_key(name),)).fetchone()
        return self._profile(row, customer_key(name), display)

    def customer_profiles(self) -> dict[str, CustomerProfile]:
        return {r["customer_key"]: self._profile(r) for r in self._db.execute("SELECT * FROM customers").fetchall()}

    def update_customer(self, name: str, actor: str, **changes) -> CustomerProfile:
        """Change contact details, credit limit, preferred channel, do-not-contact or notes.

        Only the fields passed are changed. credit_limit=None clears the limit.
        """
        allowed = {"contact_person", "phone", "email", "preferred_channel", "credit_limit", "do_not_contact", "notes"}
        unknown = set(changes) - allowed
        if unknown:
            raise StoreError(f"cannot change: {', '.join(sorted(unknown))}")
        current = self.customer_profile(name)
        key, display = customer_key(name), current.name
        new = {
            "contact_person": current.contact_person, "phone": current.phone, "email": current.email,
            "preferred_channel": current.preferred_channel, "credit_limit": current.credit_limit,
            "do_not_contact": current.do_not_contact, "notes": current.notes,
        }
        try:
            for field_name, value in changes.items():
                if field_name in ("contact_person", "notes"):
                    value = str(value or "").strip()
                    if len(value) > (120 if field_name == "contact_person" else 2000):
                        raise ValueError(f"{field_name} is too long")
                elif field_name == "phone":
                    value = normalise_phone(str(value or ""), self.get_setting("default_country_code"))
                elif field_name == "email":
                    value = normalise_email(str(value or ""))
                elif field_name == "preferred_channel":
                    value = str(value or "")
                    if value not in CHANNEL_PREFERENCES:
                        raise ValueError(f"preferred channel must be one of: {', '.join(c or 'none' for c in CHANNEL_PREFERENCES)}")
                elif field_name == "credit_limit":
                    value = None if value in (None, "") else to_money(value)
                    if value is not None and value <= 0:
                        raise ValueError("credit limit must be more than zero (or empty for none)")
                elif field_name == "do_not_contact":
                    if not isinstance(value, bool):
                        raise ValueError("do_not_contact must be true or false")
                new[field_name] = value
        except ValueError as exc:
            raise StoreError(str(exc)) from None

        def shown(d):
            return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in d.items()}

        before = {"customer": display, **shown({k: getattr(current, k) for k in new})}
        after = {"customer": display, **shown(new)}
        if before == after:
            return current
        ts = self._now().isoformat(timespec="seconds")
        with self._db:
            self._db.execute(
                "INSERT INTO customers (customer_key, display_name, contact_person, phone, email, preferred_channel, "
                "credit_limit_paise, do_not_contact, notes, updated_ts) VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(customer_key) DO UPDATE SET contact_person=excluded.contact_person, phone=excluded.phone, "
                "email=excluded.email, preferred_channel=excluded.preferred_channel, credit_limit_paise=excluded.credit_limit_paise, "
                "do_not_contact=excluded.do_not_contact, notes=excluded.notes, updated_ts=excluded.updated_ts",
                (key, display, new["contact_person"], new["phone"], new["email"], new["preferred_channel"],
                 None if new["credit_limit"] is None else to_paise(new["credit_limit"]), int(new["do_not_contact"]), new["notes"], ts))
            self._audit(ts, actor, "customer.updated", None, before, after, "", entity="customer")
        return self.customer_profile(name)

    # ---- disputes ------------------------------------------------------------------------

    def open_dispute(self, invoice_no: str, customer: str, category: str, actor: str, note: str = "", owner: str = "") -> int:
        """Mark an invoice as disputed so it is not chased like an ordinary late payment."""
        if category not in DISPUTE_CATEGORIES:
            raise StoreError(f"category must be one of: {', '.join(DISPUTE_CATEGORIES)}")
        if len(note) > 2000 or len(owner) > 120:
            raise StoreError("note or owner is too long")
        key = customer_key(customer)
        row = self._db.execute("SELECT amount_paise, paid_paise, customer FROM invoices WHERE invoice_no = ? AND customer_key = ?",
                               (invoice_no, key)).fetchone()
        if row is None:
            raise StoreError(f"unknown invoice {invoice_no!r} for {customer!r}")
        if row["amount_paise"] <= row["paid_paise"]:
            raise StoreError("that invoice has nothing outstanding, so there is nothing to dispute")
        if self._db.execute("SELECT 1 FROM disputes WHERE invoice_no = ? AND customer_key = ? AND status = 'open'", (invoice_no, key)).fetchone():
            raise StoreError("that invoice already has an open dispute")
        ts = self._now().isoformat(timespec="seconds")
        with self._db:
            dispute_id = self._db.execute(
                "INSERT INTO disputes (invoice_no, customer_key, category, note, owner, opened_ts, opened_by) VALUES (?,?,?,?,?,?,?)",
                (invoice_no, key, category, note.strip(), owner.strip(), ts, actor)).lastrowid
            self._audit(ts, actor, "dispute.opened", dispute_id, None,
                        {"invoice_no": invoice_no, "customer": row["customer"], "category": category}, note.strip(), entity="dispute")
        return dispute_id

    def resolve_dispute(self, dispute_id: int, actor: str, note: str = "") -> None:
        row = self._db.execute("SELECT status, invoice_no, customer_key FROM disputes WHERE id = ?", (dispute_id,)).fetchone()
        if row is None:
            raise StoreError(f"no dispute #{dispute_id}")
        if row["status"] != "open":
            raise StoreError(f"dispute #{dispute_id} is already resolved")
        if len(note) > 2000:
            raise StoreError("note is too long")
        ts = self._now().isoformat(timespec="seconds")
        with self._db:
            self._db.execute("UPDATE disputes SET status='resolved', resolved_ts=?, resolved_by=?, resolution_note=? WHERE id=?",
                             (ts, actor, note.strip(), dispute_id))
            self._audit(ts, actor, "dispute.resolved", dispute_id, {"status": "open"}, {"status": "resolved"}, note.strip(), entity="dispute")

    def disputes(self, status: str | None = None) -> list[Dispute]:
        sql = ("SELECT d.*, i.customer AS display, (i.amount_paise - i.paid_paise) AS outstanding FROM disputes d "
               "LEFT JOIN invoices i ON i.invoice_no = d.invoice_no AND i.customer_key = d.customer_key")
        args: tuple = ()
        if status:
            sql, args = sql + " WHERE d.status = ?", (status,)
        out = []
        for r in self._db.execute(sql + " ORDER BY d.id DESC", args).fetchall():
            out.append(Dispute(r["id"], r["invoice_no"], r["display"] or r["customer_key"], r["customer_key"], r["category"], r["note"], r["owner"],
                               r["status"], r["opened_ts"], r["opened_by"], r["resolved_ts"], r["resolution_note"],
                               from_paise(r["outstanding"] or 0)))
        return out

    def disputed_invoice_keys(self) -> set[tuple[str, str]]:
        return {(r["invoice_no"], r["customer_key"]) for r in
                self._db.execute("SELECT invoice_no, customer_key FROM disputes WHERE status = 'open'").fetchall()}

    # ---- settle by confirmation ------------------------------------------------------------

    def settle_invoices(self, items: list[tuple[str, str]], actor: str, note: str = "") -> Decimal:
        """Record that these invoices were paid in full. Used only on a person's confirmation.

        Returns the total outstanding that was closed. All or nothing.
        """
        if not items:
            raise StoreError("choose at least one invoice")
        if len(items) > 1000:
            raise StoreError("at most 1000 invoices at a time")
        ts = self._now().isoformat(timespec="seconds")
        total = Decimal("0.00")
        with self._db:
            for invoice_no, customer in items:
                row = self._db.execute("SELECT * FROM invoices WHERE invoice_no = ? AND customer_key = ?", (invoice_no, customer_key(customer))).fetchone()
                if row is None:
                    raise StoreError(f"unknown invoice {invoice_no!r} for {customer!r}")
                if row["amount_paise"] <= row["paid_paise"]:
                    raise StoreError(f"invoice {invoice_no!r} has nothing outstanding")
                keys = ("invoice_date", "due_date", "amount_paise", "paid_paise")
                before = {k: row[k] for k in keys}
                after = {**before, "paid_paise": row["amount_paise"]}
                self._db.execute("UPDATE invoices SET paid_paise = amount_paise WHERE id = ?", (row["id"],))
                self._audit(ts, actor, "invoice.settled", row["id"], before, after, note.strip() or "confirmed paid in full")
                total += from_paise(row["amount_paise"] - row["paid_paise"])
        return total

    # ---- promise history, reliability, and payments seen since a promise -----------------------

    def promise_records(self, customer: str | None = None) -> list[PromiseRecord]:
        sql = ("SELECT p.*, f.ts AS created_ts FROM promises p JOIN followups f ON f.id = p.followup_id")
        args: tuple = ()
        if customer is not None:
            sql, args = sql + " WHERE p.customer_key = ?", (customer_key(customer),)
        return [PromiseRecord(r["id"], r["customer"], r["customer_key"], date.fromisoformat(r["promised_date"]),
                              None if r["promised_paise"] is None else from_paise(r["promised_paise"]), r["status"],
                              r["created_ts"], r["resolved_ts"], r["resolution_note"])
                for r in self._db.execute(sql + " ORDER BY p.id", args).fetchall()]

    def reliability(self, as_of: date) -> dict[str, Reliability]:
        """Per customer: promises kept vs. broken (marked broken, or open and past their date)."""
        counts: dict[str, list[int]] = {}
        for p in self.promise_records():
            slot = counts.setdefault(p.customer_key, [0, 0])
            if p.status == "kept":
                slot[0] += 1
            elif p.status == "broken" or (p.status == "open" and p.promised_date < as_of):
                slot[1] += 1
        return {k: Reliability(*v) for k, v in counts.items() if v[0] + v[1] > 0}

    def payments_since(self, key: str, since_ts: str) -> Decimal:
        """Sum of increases in a customer's recorded paid amounts that arrived after since_ts."""
        rows = self._db.execute(
            "SELECT a.before_json, a.after_json FROM audit_log a JOIN invoices i ON i.id = a.entity_id "
            "WHERE a.entity = 'invoice' AND a.action IN (?, ?) AND i.customer_key = ? AND a.ts > ?",
            (*_PAYMENT_ACTIONS, key, since_ts)).fetchall()
        paise = 0
        for r in rows:
            delta = json.loads(r["after_json"])["paid_paise"] - json.loads(r["before_json"])["paid_paise"]
            if delta > 0:
                paise += delta
        return from_paise(paise)

    def promise_suggestions(self) -> list[PromiseSuggestion]:
        """Open promises where payments have since arrived. Suggestions only; a person marks it kept."""
        out = []
        for p in self.promise_records():
            if p.status != "open":
                continue
            observed = self.payments_since(p.customer_key, p.created_ts)
            if observed <= 0:
                continue
            kind = "looks_kept" if p.promised_amount is None or observed >= p.promised_amount else "partial"
            out.append(PromiseSuggestion(p, observed, kind))
        return out

    # ---- read-only import diff ------------------------------------------------------------------

    def diff_import(self, report: ImportReport) -> ImportDiff:
        """What applying this import would change. Writes nothing."""
        diff = ImportDiff()
        seen: set[tuple[str, str]] = set()
        for item in report.invoices:
            inv = item.invoice
            key = (inv.invoice_no, customer_key(inv.customer))
            seen.add(key)
            row = self._db.execute("SELECT * FROM invoices WHERE invoice_no = ? AND customer_key = ?", key).fetchone()
            if row is None:
                diff.new.append((inv.invoice_no, inv.customer, inv.amount))
                continue
            before_due = None if row["due_date"] is None else date.fromisoformat(row["due_date"])
            change = InvoiceChange(inv.invoice_no, inv.customer, from_paise(row["paid_paise"]), inv.paid,
                                   from_paise(row["amount_paise"]), inv.amount, before_due, inv.due_date)
            same = (to_paise(change.paid_after) == row["paid_paise"] and to_paise(change.amount_after) == row["amount_paise"]
                    and before_due == inv.due_date and row["invoice_date"] == inv.invoice_date.isoformat())
            if same:
                diff.unchanged += 1
                continue
            diff.changed.append(change)
            if change.paid_delta > 0:
                diff.payments_seen += change.paid_delta
            elif change.paid_delta < 0:
                diff.reversals.append(change)
        for row in self._db.execute("SELECT invoice_no, customer, customer_key, amount_paise, paid_paise FROM invoices "
                                    "WHERE amount_paise > paid_paise ORDER BY id").fetchall():
            if (row["invoice_no"], row["customer_key"]) not in seen:
                diff.missing.append((row["invoice_no"], row["customer"], from_paise(row["amount_paise"] - row["paid_paise"])))
        return diff

    # ---- activity feed ---------------------------------------------------------------------------

    def activity(self, limit: int = 100, before_id: int | None = None) -> list[dict]:
        """Newest-first audit entries with a human label for what each one touched."""
        limit = max(1, min(int(limit), 500))
        args: list = []
        sql = "SELECT * FROM audit_log"
        if before_id is not None:
            sql += " WHERE id < ?"
            args.append(int(before_id))
        rows = self._db.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        invoices = {r["id"]: f"{r['invoice_no']} · {r['customer']}" for r in self._db.execute("SELECT id, invoice_no, customer FROM invoices")}
        promises = {r["id"]: r["customer"] for r in self._db.execute("SELECT id, customer FROM promises")}
        followups = {r["id"]: r["customer"] for r in self._db.execute("SELECT id, customer FROM followups")}
        out = []
        for r in rows:
            before = json.loads(r["before_json"]) if r["before_json"] else None
            after = json.loads(r["after_json"]) if r["after_json"] else None
            label = ""
            if r["entity"] == "invoice":
                label = invoices.get(r["entity_id"], "")
            elif r["entity"] == "promise":
                label = promises.get(r["entity_id"], "")
            elif r["entity"] == "followup":
                label = followups.get(r["entity_id"], "")
            elif r["entity"] == "dispute" and after:
                label = f"{after.get('invoice_no', '')} · {after.get('customer', '')}".strip(" ·")
            if not label:
                label = (after or {}).get("customer") or (before or {}).get("customer") or ""
            out.append({"id": r["id"], "ts": r["ts"], "actor": r["actor"], "action": r["action"], "entity": r["entity"],
                        "label": label, "reason": r["reason"], "before": before, "after": after})
        return out
