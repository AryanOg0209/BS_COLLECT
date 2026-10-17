"""Cheque register, TDS register, customer groups and the v5 -> v6 migration."""

import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from collectos.importer import import_rows
from collectos.store import SCHEMA_VERSION, Store, StoreError
from collectos.taxcal import fy_quarter, valid_fy

HEADER = ["Invoice No", "Customer", "Invoice Date", "Due Date", "Amount", "Paid"]
NOW = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
D = Decimal


@pytest.fixture
def store(tmp_path):
    rows = [(1, HEADER), (2, ["INV-1", "Acme Traders", "01-06-2026", "01-07-2026", "50000", "0"]), (3, ["INV-2", "Beta Co", "01-06-2026", "01-08-2026", "20000", "0"])]
    with Store(tmp_path / "r.db", now=lambda: NOW) as s:
        s.apply_import(import_rows(rows), actor="t")
        yield s


# ---- financial year helpers -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("on, expected", [(date(2026, 4, 1), ("2026-27", 1)), (date(2026, 6, 30), ("2026-27", 1)), (date(2026, 7, 1), ("2026-27", 2)),
                                           (date(2026, 10, 14), ("2026-27", 3)), (date(2026, 12, 31), ("2026-27", 3)), (date(2027, 1, 1), ("2026-27", 4)),
                                           (date(2027, 3, 31), ("2026-27", 4)), (date(2027, 4, 1), ("2027-28", 1)), (date(2099, 12, 1), ("2099-00", 3))])
def test_financial_year_and_quarter(on, expected):
    assert fy_quarter(on) == expected


def test_fy_validation():
    assert valid_fy("2026-27") and valid_fy("2099-00")
    assert not any(valid_fy(x) for x in ("2026-28", "26-27", "2026/27", "abcd-ef", "2026-2027", ""))


# ---- migration ---------------------------------------------------------------------------------------------------------------

def test_a_v5_database_upgrades_in_place_and_keeps_its_data(tmp_path):
    path = tmp_path / "old.db"
    with Store(path, now=lambda: NOW) as s:
        s.apply_import(import_rows([(1, HEADER), (2, ["INV-1", "Acme", "01-06-2026", "01-07-2026", "100", "0"])]), actor="t")
        s.update_customer("Acme", "t", phone="9876543210")
    raw = sqlite3.connect(path)
    raw.executescript("DROP TRIGGER cheques_no_delete; DROP TRIGGER tds_no_delete; DROP TABLE cheques; DROP TABLE tds_entries; "
                      "ALTER TABLE customers DROP COLUMN group_name; PRAGMA user_version = 5;")
    raw.close()
    with Store(path, now=lambda: NOW) as s:
        assert s.invoices()[0].invoice_no == "INV-1" and s.customer_profile("Acme").phone == "+919876543210"
        assert s.customer_profile("Acme").group == "" and s.cheques() == [] and s.tds_entries() == []
        assert s._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 6


# ---- customer groups ----------------------------------------------------------------------------------------------------------

def test_customer_group_is_saved_normalised_and_audited(store):
    p = store.update_customer("acme traders", "t", group="  Gupta   Group ")
    assert p.group == "Gupta Group" and store.customer_profile("Acme Traders").group == "Gupta Group"
    store.update_customer("Acme Traders", "t", group="")
    assert store.customer_profile("Acme Traders").group == ""
    assert any(e["action"] == "customer.updated" for e in store.activity(20))
    with pytest.raises(StoreError, match="too long"):
        store.update_customer("Acme Traders", "t", group="g" * 121)


# ---- cheques ----------------------------------------------------------------------------------------------------------------

def test_cheque_lifecycle_follows_the_allowed_steps(store):
    cid = store.add_cheque("acme traders", "000123", "25000", date(2026, 10, 20), "t", bank="HDFC", invoice_nos="INV-1")
    c = store.cheques()[0]
    assert (c.id, c.customer, c.amount, c.status, c.cheque_date) == (cid, "Acme Traders", D("25000.00"), "received", date(2026, 10, 20))
    with pytest.raises(StoreError, match="can become: deposited, returned"):
        store.set_cheque_status(cid, "cleared", "t")
    assert store.set_cheque_status(cid, "deposited", "t").status == "deposited"
    assert store.set_cheque_status(cid, "bounced", "t", "insufficient funds").status == "bounced"
    assert store.set_cheque_status(cid, "deposited", "t").status == "deposited"        # presented again
    assert store.set_cheque_status(cid, "cleared", "t").status == "cleared"
    with pytest.raises(StoreError, match="final"):
        store.set_cheque_status(cid, "bounced", "t")
    actions = [e["action"] for e in store.activity(50)]
    assert actions.count("cheque.status") == 4 and "cheque.added" in actions


def test_cheque_validation_and_duplicates(store):
    ok = dict(customer="Acme Traders", cheque_no="1", amount="100", cheque_date=date(2026, 10, 20), actor="t", bank="SBI")
    store.add_cheque(**ok)
    with pytest.raises(StoreError, match="already in the register"):
        store.add_cheque(**ok)
    store.add_cheque(**{**ok, "bank": "HDFC"})                                       # same number, different bank
    for bad in ({"amount": "0"}, {"amount": "abc"}, {"cheque_no": " "}, {"customer": "Nobody"}, {"bank": "b" * 61}, {"amount": "1.234"}):
        with pytest.raises(StoreError):
            store.add_cheque(**{**ok, "cheque_no": "9", **bad})
    cid = store.cheques()[0].id
    store.set_cheque_status(cid, "returned", "t")
    store.add_cheque(**ok)                                                           # a returned cheque's number can be reused


def test_cheques_are_never_deleted_and_filter(store):
    store.add_cheque("Acme Traders", "1", "100", date(2026, 10, 20), "t")
    store.add_cheque("Beta Co", "2", "200", date(2026, 10, 21), "t")
    with pytest.raises(sqlite3.DatabaseError, match="never deleted"):
        store._db.execute("DELETE FROM cheques")
    assert [c.customer for c in store.cheques(customer="beta co")] == ["Beta Co"]
    assert [c.cheque_no for c in store.cheques(status="received")] == ["1", "2"] and store.cheques(status="cleared") == []


def test_a_cheque_in_hand_does_not_change_the_invoice(store):
    before = [i.outstanding for i in store.invoices()]
    cid = store.add_cheque("Acme Traders", "1", "50000", date(2026, 10, 20), "t", invoice_nos="INV-1")
    store.set_cheque_status(cid, "deposited", "t"); store.set_cheque_status(cid, "cleared", "t")
    assert [i.outstanding for i in store.invoices()] == before


# ---- TDS ----------------------------------------------------------------------------------------------------------------------

def test_tds_entry_gets_its_quarter_and_can_be_ticked_and_voided(store):
    tid = store.add_tds("Acme Traders", date(2026, 10, 5), "1224.00", "t", invoice_no="INV-1", gross="61200")
    e = store.tds_entries("2026-27")[0]
    assert (e.fy, e.quarter, e.tds, e.gross, e.in_26as, e.voided) == ("2026-27", 3, D("1224.00"), D("61200.00"), False, False)
    store.set_tds_26as(tid, True, "t")
    assert store.tds_entries()[0].in_26as is True
    store.void_tds(tid, "t", "entered twice")
    assert store.tds_entries() == [] and len(store.tds_entries(include_void=True)) == 1
    with pytest.raises(StoreError, match="voided"):
        store.set_tds_26as(tid, False, "t")
    assert [e["action"] for e in store.activity(20)].count("tds.voided") == 1


def test_tds_validation(store):
    base = dict(customer="Acme Traders", deducted_on=date(2026, 10, 5), tds="100", actor="t")
    for bad in ({"tds": "0"}, {"tds": "-5"}, {"tds": "x"}, {"customer": "Nobody"}, {"gross": "100"}, {"gross": "50"}, {"invoice_no": "i" * 61}):
        with pytest.raises(StoreError):
            store.add_tds(**{**base, **bad})
    with pytest.raises(StoreError, match="financial year"):
        store.tds_entries("2026-28")
    store.add_tds(**base)
    with pytest.raises(sqlite3.DatabaseError, match="never deleted"):
        store._db.execute("DELETE FROM tds_entries")


def test_tds_filters_by_year(store):
    store.add_tds("Acme Traders", date(2026, 3, 31), "10", "t"); store.add_tds("Acme Traders", date(2026, 4, 1), "20", "t")
    assert [e.tds for e in store.tds_entries("2025-26")] == [D("10.00")] and [e.tds for e in store.tds_entries("2026-27")] == [D("20.00")]
    assert len(store.tds_entries()) == 2
