import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from collectos import store as store_module
from collectos.importer import import_rows
from collectos.store import Store, StoreError
from collectos.workqueue import build_queue

HEADER = ["Invoice No", "Customer", "Invoice Date", "Due Date", "Amount", "Paid"]
NOW = datetime(2026, 10, 15, 9, 30, tzinfo=timezone.utc)


def seed(s):
    rows = [(1, HEADER), (2, ["A1", "Acme Traders", "01-06-2026", "01-07-2026", "50000", "0"])]
    s.apply_import(import_rows(rows), actor="a")


@pytest.fixture
def store():
    with Store(now=lambda: NOW) as s:
        seed(s)
        yield s


def test_log_followup_without_promise(store):
    fid, pid = store.log_followup("acme  traders", "call", "Phone not answered", actor="priya", note="try tomorrow")
    assert pid is None
    [f] = store.followups("ACME TRADERS")
    assert (f.id, f.customer, f.channel, f.actor, f.note) == (fid, "Acme Traders", "call", "priya", "try tomorrow")
    assert store.last_contacts() == {"acme traders": date(2026, 10, 15)}


def test_log_followup_with_promise_is_atomic_and_audited(store):
    fid, pid = store.log_followup(
        "Acme Traders", "call", "Will pay part", actor="priya",
        promise_date=date(2026, 10, 20), promise_amount=Decimal("20000"),
    )
    [p] = store.open_promises()
    assert (p.id, p.promised_date, p.promised_amount, p.status) == (pid, date(2026, 10, 20), Decimal("20000.00"), "open")
    actions = [e.action for e in store.audit_entries() if e.entity in ("followup", "promise")]
    assert actions == ["followup.logged", "promise.created"]


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"customer": "Nobody Ltd"}, "unknown customer"),
        ({"channel": "carrier-pigeon"}, "channel must be one of"),
        ({"outcome": "   "}, "outcome is required"),
        ({"promise_date": date(2026, 10, 14)}, "in the past"),
        ({"promise_amount": Decimal("100")}, "without promise_date"),
        ({"promise_date": date(2026, 10, 20), "promise_amount": Decimal("0")}, "must be positive"),
    ],
)
def test_invalid_followups_are_rejected_and_write_nothing(store, kwargs, message):
    args = dict(customer="Acme Traders", channel="call", outcome="ok", actor="a")
    args.update(kwargs)
    with pytest.raises(StoreError, match=message):
        store.log_followup(**args)
    assert store.followups() == [] and store.open_promises() == []


def test_promise_today_is_allowed(store):
    store.log_followup("Acme Traders", "call", "pays today", actor="a", promise_date=date(2026, 10, 15))
    assert len(store.open_promises()) == 1


def test_resolve_promise_and_cannot_resolve_twice(store):
    _, pid = store.log_followup("Acme Traders", "call", "x", actor="a", promise_date=date(2026, 10, 20))
    store.resolve_promise(pid, "kept", actor="priya", note="NEFT received")
    assert store.open_promises() == []
    last = store.audit_entries()[-1]
    assert (last.action, last.before, last.after, last.reason) == ("promise.kept", {"status": "open"}, {"status": "kept"}, "NEFT received")
    with pytest.raises(StoreError, match="already kept"):
        store.resolve_promise(pid, "broken", actor="a")


def test_resolve_validation(store):
    with pytest.raises(StoreError, match="status must be"):
        store.resolve_promise(1, "paid", actor="a")
    with pytest.raises(StoreError, match="no promise"):
        store.resolve_promise(999, "kept", actor="a")


def test_followups_and_promises_cannot_be_tampered_with(store):
    store.log_followup("Acme Traders", "call", "x", actor="a", promise_date=date(2026, 10, 20))
    for sql in ("UPDATE followups SET outcome='y'", "DELETE FROM followups", "DELETE FROM promises"):
        with pytest.raises(sqlite3.DatabaseError):
            store._db.execute(sql)


def test_end_to_end_into_the_work_queue(store):
    # Day 0: contact + promise for 20 Oct. Queue: waiting.
    store.log_followup("Acme Traders", "call", "promised", actor="a", promise_date=date(2026, 10, 20))
    q = build_queue(store.invoices(), date(2026, 10, 18), store.last_contacts(), store.open_promises())
    assert q.work == [] and "promised to pay by 20-Oct-2026" in q.waiting[0].reason
    # Day 6 after the date passes unpaid: broken promise surfaces at the top, with reasons.
    q = build_queue(store.invoices(), date(2026, 10, 21), store.last_contacts(), store.open_promises())
    [item] = q.work
    assert item.action == "Call" and any("broken promise" in r for r in item.reasons)


def test_migrates_a_v1_database_to_v2_without_losing_data(tmp_path):
    db = tmp_path / "v1.db"
    raw = sqlite3.connect(db)
    raw.executescript(store_module._SCHEMA_V1)
    raw.execute("PRAGMA user_version = 1")
    raw.execute("INSERT INTO imports (source, imported_at, actor) VALUES ('old.csv','2026-01-01T00:00:00+00:00','a')")
    raw.execute(
        "INSERT INTO invoices (invoice_no, customer, customer_key, invoice_date, due_date, amount_paise, paid_paise, "
        "first_import_id, last_import_id) VALUES ('A1','Acme','acme','2026-01-01','2026-02-01',100000,0,1,1)"
    )
    raw.commit()
    raw.close()

    with Store(db, now=lambda: NOW) as s:
        assert s.invoices()[0].amount == Decimal("1000.00")
        s.log_followup("Acme", "email", "sent reminder", actor="a")  # v2 table is usable
        assert s._db.execute("PRAGMA user_version").fetchone()[0] == store_module.SCHEMA_VERSION == 3
