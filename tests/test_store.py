import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from collectos.importer import import_rows
from collectos.store import Store, StoreError, from_paise, to_paise

HEADER = ["Invoice No", "Customer", "Invoice Date", "Due Date", "Amount", "Paid"]
FIXED_NOW = datetime(2026, 10, 15, 9, 30, tzinfo=timezone.utc)


def report(*rows, source="week1.csv", **kw):
    data = [(1, HEADER)] + [(i, list(r)) for i, r in enumerate(rows, start=2)]
    return import_rows(data, source=source, **kw)


def A1(paid="0", amount="1000"):
    return ["A1", "Acme", "01-09-2026", "01-10-2026", amount, paid]


@pytest.fixture
def store():
    with Store(now=lambda: FIXED_NOW) as s:
        yield s


def test_paise_round_trip_is_exact():
    for text in ["0.01", "0.10", "1.00", "99999999.99", "123456.78"]:
        d = Decimal(text)
        assert from_paise(to_paise(d)) == d
    assert to_paise(Decimal("0.10")) + to_paise(Decimal("0.20")) == to_paise(Decimal("0.30"))


def test_insert_and_read_back(store):
    result = store.apply_import(report(A1(paid="250.50")), actor="aryan")
    assert (result.inserted, result.updated, result.unchanged) == (1, 0, 0)
    [inv] = store.invoices()
    assert (inv.invoice_no, inv.customer, inv.due_date) == ("A1", "Acme", date(2026, 10, 1))
    assert inv.amount == Decimal("1000.00") and inv.paid == Decimal("250.50")


def test_creation_is_audited(store):
    store.apply_import(report(A1()), actor="aryan")
    [entry] = store.audit_entries()
    assert (entry.action, entry.actor, entry.ts) == ("invoice.created", "aryan", "2026-10-15T09:30:00+00:00")
    assert entry.before is None and entry.after["amount_paise"] == 100000
    assert "import #1 row 2" in entry.reason


def test_identical_reimport_changes_nothing(store):
    store.apply_import(report(A1()), actor="aryan")
    again = store.apply_import(report(A1(), source="week2.csv"), actor="aryan")
    assert (again.inserted, again.updated, again.unchanged) == (0, 0, 1)
    assert len(store.invoices()) == 1
    assert len(store.audit_entries()) == 1  # no noise audit rows


def test_changed_invoice_is_updated_with_before_and_after(store):
    store.apply_import(report(A1(paid="0")), actor="aryan")
    result = store.apply_import(report(A1(paid="400"), source="week2.csv"), actor="priya")
    assert result.updated == 1
    assert store.invoices()[0].paid == Decimal("400.00")
    update = store.audit_entries()[-1]
    assert update.action == "invoice.updated" and update.actor == "priya"
    assert update.before["paid_paise"] == 0 and update.after["paid_paise"] == 40000


def test_decreased_paid_amount_raises_a_warning(store):
    store.apply_import(report(A1(paid="400")), actor="a")
    result = store.apply_import(report(A1(paid="100"), source="w2"), actor="a")
    assert any("possible reversal" in w for w in result.warnings)


def test_same_invoice_matches_across_customer_case_and_spacing(store):
    store.apply_import(report(["A1", "Acme  Traders", "01-09-2026", "01-10-2026", "100", "0"]), actor="a")
    result = store.apply_import(report(["A1", "ACME TRADERS", "01-09-2026", "01-10-2026", "100", "0"], source="w2"), actor="a")
    assert result.unchanged == 1 and len(store.invoices()) == 1


def test_absent_invoices_are_listed_never_deleted(store):
    store.apply_import(report(A1(), ["B1", "Beta", "01-09-2026", "01-10-2026", "50", "0"]), actor="a")
    result = store.apply_import(report(A1(), source="w2"), actor="a")
    assert result.missing_from_import == ["B1 (Beta)"]
    assert len(store.invoices()) == 2


def test_import_with_errors_is_refused_by_default(store):
    bad = report(A1(), ["X", "Bad", "not-a-date", "01-10-2026", "10", "0"])
    with pytest.raises(StoreError, match="error row"):
        store.apply_import(bad, actor="a")
    assert store.invoices() == []


def test_allow_errors_keeps_good_rows_and_preserves_rejected_source_rows(store):
    bad = report(A1(), A1(), ["X", "Bad", "not-a-date", "01-10-2026", "10", "0"])
    result = store.apply_import(bad, actor="a", allow_errors=True)
    assert result.inserted == 1
    rows = {(r["row_no"], r["disposition"]) for r in store.source_rows(result.import_id)}
    assert rows == {(2, "accepted"), (3, "duplicate"), (4, "rejected")}
    rejected = [r for r in store.source_rows(result.import_id) if r["disposition"] == "rejected"][0]
    assert "unrecognised date" in rejected["note"] and "not-a-date" in rejected["raw_json"]


def test_failure_mid_import_rolls_everything_back(store, monkeypatch):
    calls = {"n": 0}
    original = store._audit

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "_audit", flaky)
    with pytest.raises(RuntimeError):
        store.apply_import(report(A1(), ["B1", "Beta", "01-09-2026", "01-10-2026", "50", "0"]), actor="a")
    assert store.invoices() == [] and store.audit_entries() == []
    assert store._db.execute("SELECT COUNT(*) FROM imports").fetchone()[0] == 0


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE audit_log SET actor = 'mallory'",
        "DELETE FROM audit_log",
        "UPDATE source_rows SET raw_json = '{}'",
        "DELETE FROM source_rows",
    ],
)
def test_audit_and_source_rows_are_append_only(store, sql):
    store.apply_import(report(A1()), actor="a")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._db.execute(sql)


def test_database_rejects_invalid_money_directly(store):
    store.apply_import(report(A1()), actor="a")
    with pytest.raises(sqlite3.IntegrityError):
        store._db.execute("UPDATE invoices SET amount_paise = 0")


def test_filled_due_date_is_recorded_in_audit_reason(store):
    store.apply_import(report(["A1", "Acme", "01-09-2026", "", "100", "0"], default_credit_days=30), actor="a")
    assert "due date filled from default credit days" in store.audit_entries()[0].reason
    assert store.invoices()[0].due_date == date(2026, 10, 1)


def test_persists_across_reopen_and_backup_is_readable(tmp_path):
    db = tmp_path / "collectos.db"
    with Store(db, now=lambda: FIXED_NOW) as s:
        s.apply_import(report(A1(paid="10.25")), actor="a")
        s.backup(tmp_path / "backup.db")
    with Store(db) as reopened:
        assert reopened.invoices()[0].paid == Decimal("10.25")
    with Store(tmp_path / "backup.db") as restored:
        assert restored.invoices()[0].amount == Decimal("1000.00")
        assert len(restored.audit_entries()) == 1


def test_unsupported_schema_version_is_refused(tmp_path):
    db = tmp_path / "old.db"
    raw = sqlite3.connect(db)
    raw.execute("PRAGMA user_version = 99")
    raw.commit()
    raw.close()
    with pytest.raises(StoreError, match="not supported"):
        Store(db)
