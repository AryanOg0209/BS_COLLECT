import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from collectos import store as store_module
from collectos.importer import import_rows
from collectos.store import Store

HEADER = ["Invoice No", "Customer", "Invoice Date", "Due Date", "Amount", "Paid"]
D0 = date(2026, 10, 1)


class Clock:
    def __init__(self, day=D0):
        self.day = day

    def __call__(self):
        return datetime(self.day.year, self.day.month, self.day.day, 10, 0, tzinfo=timezone.utc)


def rows(*invoices):
    return import_rows([(1, HEADER)] + [(i, list(r)) for i, r in enumerate(invoices, start=2)])


# due dates relative to D0: A1 due 01-07 (long overdue), B1 due 01-09, C1 not due until 01-11
A1 = ["A1", "Acme", "01-06-2026", "01-07-2026", "100000", "0"]
B1 = ["B1", "Beta", "01-08-2026", "01-09-2026", "40000", "0"]
C1 = ["C1", "Cee", "01-09-2026", "01-11-2026", "10000", "0"]


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(clock):
    with Store(now=clock) as s:
        yield s


def test_baseline_snapshot_measures_ageing_exactly(store):
    store.apply_import(rows(A1, B1, C1), actor="a")
    snap = store.take_snapshot(D0, actor="a")
    assert snap.total_outstanding == Decimal("150000.00")
    assert snap.overdue == Decimal("140000.00")
    # A1 is 92 days overdue, B1 is 30 days overdue (not > 30)
    assert snap.over30 == Decimal("100000.00") and snap.over60 == Decimal("100000.00") and snap.over90 == Decimal("100000.00")
    assert (snap.open_invoices, snap.overdue_invoices, snap.overdue_customers) == (3, 2, 2)
    assert snap.payments_observed == Decimal("0.00")  # first snapshot is a baseline


def test_payments_observed_comes_from_audited_paid_increases_only(store, clock):
    store.apply_import(rows(A1, B1), actor="a")
    store.take_snapshot(D0, actor="a")

    clock.day = D0 + timedelta(days=7)
    a1_paid = [*A1[:5], "25000.50"]
    store.apply_import(rows(a1_paid, B1), actor="a")
    snap = store.take_snapshot(clock.day, actor="a")
    assert snap.payments_observed == Decimal("25000.50")
    assert snap.total_outstanding == Decimal("114999.50")


def test_vanished_invoices_are_not_counted_as_payments(store, clock):
    store.apply_import(rows(A1, B1), actor="a")
    store.take_snapshot(D0, actor="a")
    clock.day = D0 + timedelta(days=7)
    store.apply_import(rows(B1), actor="a")  # A1 absent: maybe paid, maybe not; we do not guess
    assert store.take_snapshot(clock.day, actor="a").payments_observed == Decimal("0.00")


def test_payments_are_counted_once_per_period(store, clock):
    store.apply_import(rows(A1), actor="a")
    store.take_snapshot(D0, actor="a")
    clock.day = D0 + timedelta(days=7)
    store.apply_import(rows([*A1[:5], "1000"]), actor="a")
    assert store.take_snapshot(clock.day, actor="a").payments_observed == Decimal("1000.00")
    clock.day = D0 + timedelta(days=14)
    assert store.take_snapshot(clock.day, actor="a").payments_observed == Decimal("0.00")  # nothing new


def test_reversals_do_not_reduce_payments_observed(store, clock):
    store.apply_import(rows([*A1[:5], "5000"]), actor="a")
    store.take_snapshot(D0, actor="a")
    clock.day = D0 + timedelta(days=7)
    store.apply_import(rows([*A1[:5], "2000"]), actor="a")  # paid went down
    assert store.take_snapshot(clock.day, actor="a").payments_observed == Decimal("0.00")


def test_followups_and_promises_are_measured(store, clock):
    store.apply_import(rows(A1, B1), actor="a")
    clock.day = D0 + timedelta(days=1)
    store.log_followup("Acme", "call", "x", actor="a", promise_date=D0 + timedelta(days=3))
    store.log_followup("Beta", "call", "y", actor="a")
    snap = store.take_snapshot(D0 + timedelta(days=1), actor="a")
    assert snap.followups_7d == 2 and snap.open_promises == 1 and snap.broken_promises == 0
    later = store.take_snapshot(D0 + timedelta(days=20), actor="a")
    assert later.followups_7d == 0 and later.broken_promises == 1  # the promise date passed unmet


def test_snapshots_are_immutable(store):
    store.apply_import(rows(A1), actor="a")
    store.take_snapshot(D0, actor="a")
    for sql in ("UPDATE snapshots SET overdue_paise = 0", "DELETE FROM snapshots"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            store._db.execute(sql)


def test_retaking_the_same_as_of_supersedes_without_deleting(store, clock):
    store.apply_import(rows(A1), actor="a")
    first = store.take_snapshot(D0, actor="a")
    store.apply_import(rows([*A1[:5], "100"]), actor="a")
    second = store.take_snapshot(D0, actor="a")
    assert [s.id for s in store.snapshots()] == [second.id] and second.id > first.id
    assert store._db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2  # history kept


def test_snapshots_are_listed_oldest_first(store, clock):
    store.apply_import(rows(A1), actor="a")
    store.take_snapshot(D0 + timedelta(days=14), actor="a")
    store.take_snapshot(D0, actor="a")
    assert [s.as_of for s in store.snapshots()] == [D0, D0 + timedelta(days=14)]


def test_v2_database_migrates_to_v3(tmp_path):
    db = tmp_path / "v2.db"
    raw = sqlite3.connect(db)
    raw.executescript(store_module._SCHEMA_V1 + store_module._SCHEMA_V2)
    raw.execute("PRAGMA user_version = 2")
    raw.commit()
    raw.close()
    with Store(db, now=Clock()) as s:
        assert s._db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert s.take_snapshot(D0, actor="a").open_invoices == 0
