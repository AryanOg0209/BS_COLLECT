import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from collectos import store as store_module
from collectos.contacts import mailto_link, normalise_email, normalise_phone, tel_link, whatsapp_link
from collectos.errors import StoreError
from collectos.importer import import_rows
from collectos.store import Store

HEADER = ["Invoice No", "Customer", "Invoice Date", "Due Date", "Amount", "Paid"]
D0 = date(2026, 10, 1)


class Clock:
    def __init__(self, day=D0):
        self.day = day

    def __call__(self):
        return datetime(self.day.year, self.day.month, self.day.day, 10, 0, tzinfo=timezone.utc)

    def tick(self, days=0, hours=0):
        self.day += timedelta(days=days)


def rows(*invoices):
    return import_rows([(1, HEADER)] + [(i, list(r)) for i, r in enumerate(invoices, start=2)])


A1 = ["A1", "Acme Traders", "01-06-2026", "01-07-2026", "100000", "0"]
A2 = ["A2", "Acme Traders", "01-08-2026", "01-09-2026", "40000", "0"]
B1 = ["B1", "Beta Co", "01-08-2026", "01-09-2026", "20000", "5000"]


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(clock):
    with Store(now=clock) as s:
        s.apply_import(rows(A1, A2, B1), actor="t")
        yield s


# ---- contact helpers ----------------------------------------------------------------------


@pytest.mark.parametrize("raw, expected", [
    ("98765 43210", "+919876543210"), ("09876543210", "+919876543210"), ("+91 98765-43210", "+919876543210"),
    ("0091 9876543210", "+919876543210"), ("+44 20 7946 0958", "+442079460958"), ("", ""), ("  ", ""),
])
def test_phone_normalisation(raw, expected):
    assert normalise_phone(raw) == expected


@pytest.mark.parametrize("raw", ["call me", "12345", "9" * 20, "98765x43210", "<script>"])
def test_bad_phones_rejected(raw):
    with pytest.raises(ValueError):
        normalise_phone(raw)


def test_email_validation():
    assert normalise_email(" a@b.co ") == "a@b.co" and normalise_email("") == ""
    for bad in ("a@b", "no at.com", "a@@b.com", "x" * 260 + "@b.com"):
        with pytest.raises(ValueError):
            normalise_email(bad)


def test_links_are_encoded_and_none_when_missing():
    link = whatsapp_link("+919876543210", "Hello & welcome?\nSecond line ₹5,000")
    assert link.startswith("https://wa.me/919876543210?text=") and "&" not in link.split("text=")[1] and "%0A" in link
    assert whatsapp_link("", "x") is None and mailto_link("", "s", "b") is None and tel_link("") is None
    assert mailto_link("a@b.co", "Re: pay", "Hi there").startswith("mailto:a@b.co?subject=Re%3A%20pay&body=Hi%20there")
    assert tel_link("+919876543210") == "tel:+919876543210"


# ---- customer master ------------------------------------------------------------------------


def test_customer_profile_defaults_for_a_known_customer(store):
    p = store.customer_profile("ACME  traders")
    assert (p.name, p.phone, p.credit_limit, p.do_not_contact) == ("Acme Traders", "", None, False)


def test_update_customer_validates_normalises_and_audits(store):
    p = store.update_customer("acme traders", "priya", phone="98765 43210", email="acct@acme.in", contact_person="Mr Gupta",
                              preferred_channel="whatsapp", credit_limit="1,50,000", notes="Pays after the 10th")
    assert (p.phone, p.email, p.credit_limit, p.preferred_channel) == ("+919876543210", "acct@acme.in", Decimal("150000.00"), "whatsapp")
    entry = [e for e in store.audit_entries() if e.action == "customer.updated"][0]
    assert entry.actor == "priya" and entry.before["phone"] == "" and entry.after["phone"] == "+919876543210"
    assert entry.after["credit_limit"] == "150000.00"


def test_unchanged_update_writes_no_audit(store):
    store.update_customer("Acme Traders", "a", phone="9876543210")
    n = len(store.audit_entries())
    store.update_customer("Acme Traders", "a", phone="+91 9876543210")
    assert len(store.audit_entries()) == n


def test_partial_update_keeps_other_fields_and_credit_limit_can_be_cleared(store):
    store.update_customer("Acme Traders", "a", phone="9876543210", credit_limit="50000")
    p = store.update_customer("Acme Traders", "a", email="x@y.in")
    assert p.phone == "+919876543210" and p.credit_limit == Decimal("50000.00")
    assert store.update_customer("Acme Traders", "a", credit_limit=None).credit_limit is None


def test_do_not_contact_flag(store):
    assert store.update_customer("Beta Co", "a", do_not_contact=True).do_not_contact is True
    assert store.customer_profiles()["beta co"].do_not_contact is True


@pytest.mark.parametrize("changes, fragment", [
    ({"phone": "abc"}, "phone may contain"), ({"email": "nope"}, "email address"), ({"credit_limit": "0"}, "more than zero"),
    ({"credit_limit": "abc"}, "not a valid amount"), ({"preferred_channel": "pigeon"}, "preferred channel"),
    ({"do_not_contact": "yes"}, "true or false"), ({"notes": "x" * 2001}, "too long"), ({"colour": "red"}, "cannot change"),
])
def test_update_customer_rejections(store, changes, fragment):
    with pytest.raises(StoreError, match=fragment):
        store.update_customer("Acme Traders", "a", **changes)
    assert store.customer_profile("Acme Traders").phone == ""


def test_unknown_customer_refused_and_nothing_created(store):
    with pytest.raises(StoreError, match="unknown customer"):
        store.update_customer("Nobody", "a", phone="9876543210")
    assert store.customer_profiles() == {}


def test_default_country_code_setting_applies(store):
    store.set_setting("default_country_code", "44", "a")
    assert store.update_customer("Acme Traders", "a", phone="02079460958").phone == "+442079460958"


def test_settings_validated_and_audited(store):
    assert store.get_setting("msme_supplier") == "0"
    store.set_setting("msme_supplier", "1", "a")
    assert store.get_setting("msme_supplier") == "1"
    assert store.audit_entries()[-1].action == "setting.changed"
    n = len(store.audit_entries())
    store.set_setting("msme_supplier", "1", "a")
    assert len(store.audit_entries()) == n
    for key, value in (("msme_supplier", "maybe"), ("default_country_code", "abc"), ("nonsense", "1")):
        with pytest.raises(StoreError):
            store.set_setting(key, value, "a")
    with pytest.raises(StoreError):
        store.get_setting("nonsense")


# ---- disputes ---------------------------------------------------------------------------------


def test_dispute_lifecycle(store):
    d = store.open_dispute("A1", "acme traders", "price", "a", note="Rate differs from quote", owner="Rohit")
    [dispute] = store.disputes("open")
    assert (dispute.id, dispute.category, dispute.owner, dispute.outstanding) == (d, "price", "Rohit", Decimal("100000.00"))
    assert store.disputed_invoice_keys() == {("A1", "acme traders")}
    store.resolve_dispute(d, "a", "Credit note issued")
    assert store.disputes("open") == [] and store.disputed_invoice_keys() == set()
    assert store.disputes("resolved")[0].resolution_note == "Credit note issued"


@pytest.mark.parametrize("args, fragment", [
    (("ZZ", "Acme Traders", "price"), "unknown invoice"), (("A1", "Acme Traders", "rudeness"), "category must be"),
])
def test_dispute_rejections(store, args, fragment):
    with pytest.raises(StoreError, match=fragment):
        store.open_dispute(*args, actor="a")


def test_cannot_dispute_twice_or_a_settled_invoice_and_can_reopen_after_resolving(store):
    d = store.open_dispute("A1", "Acme Traders", "price", "a")
    with pytest.raises(StoreError, match="already has an open dispute"):
        store.open_dispute("A1", "Acme Traders", "quality", "a")
    store.resolve_dispute(d, "a")
    with pytest.raises(StoreError, match="already resolved"):
        store.resolve_dispute(d, "a")
    store.open_dispute("A1", "Acme Traders", "quality", "a")  # a new dispute is allowed once the first is resolved
    store.settle_invoices([("A2", "Acme Traders")], "a")
    with pytest.raises(StoreError, match="nothing outstanding"):
        store.open_dispute("A2", "Acme Traders", "price", "a")


def test_disputes_cannot_be_deleted(store):
    store.open_dispute("A1", "Acme Traders", "price", "a")
    with pytest.raises(sqlite3.DatabaseError, match="cannot be deleted"):
        store._db.execute("DELETE FROM disputes")


# ---- settle by confirmation -----------------------------------------------------------------------


def test_settle_closes_the_balance_and_counts_as_a_payment_seen(store, clock):
    store.take_snapshot(D0, "a")
    clock.tick(7)
    total = store.settle_invoices([("A1", "Acme Traders"), ("B1", "beta co")], "priya", "confirmed by accounts")
    assert total == Decimal("115000.00")                         # 100000 + (20000 - 5000)
    assert {i.invoice_no: i.outstanding for i in store.invoices()} == {"A1": Decimal("0.00"), "A2": Decimal("40000.00"), "B1": Decimal("0.00")}
    snap = store.take_snapshot(clock.day, "a")
    assert snap.payments_observed == Decimal("115000.00")
    entry = [e for e in store.audit_entries() if e.action == "invoice.settled"][0]
    assert entry.actor == "priya" and entry.reason == "confirmed by accounts" and entry.after["paid_paise"] == 10000000


def test_settle_is_all_or_nothing(store):
    with pytest.raises(StoreError, match="unknown invoice"):
        store.settle_invoices([("A1", "Acme Traders"), ("NOPE", "Acme Traders")], "a")
    assert next(i for i in store.invoices() if i.invoice_no == "A1").outstanding == Decimal("100000.00")
    with pytest.raises(StoreError, match="choose at least one"):
        store.settle_invoices([], "a")


def test_settling_twice_is_refused(store):
    store.settle_invoices([("A1", "Acme Traders")], "a")
    with pytest.raises(StoreError, match="nothing outstanding"):
        store.settle_invoices([("A1", "Acme Traders")], "a")


# ---- promise history, reliability, suggestions ----------------------------------------------------------


def test_reliability_counts_kept_broken_and_open_past_due(store, clock):
    store.log_followup("Acme Traders", "call", "x", "a", promise_date=D0 + timedelta(days=3))     # will be kept
    store.log_followup("Acme Traders", "call", "y", "a", promise_date=D0 + timedelta(days=4))     # will be marked broken
    store.log_followup("Acme Traders", "call", "z", "a", promise_date=D0 + timedelta(days=5))     # left open
    store.resolve_promise(1, "kept", "a")
    store.resolve_promise(2, "broken", "a")
    r = store.reliability(D0 + timedelta(days=2))["acme traders"]
    assert (r.kept, r.broken, r.total) == (1, 1, 2)                 # the open one has not passed yet
    r = store.reliability(D0 + timedelta(days=9))["acme traders"]
    assert (r.kept, r.broken) == (1, 2)                              # now it counts as broken
    assert store.promise_records("acme traders")[0].status == "kept" and len(store.promise_records()) == 3


def test_cancelled_promises_do_not_count_either_way(store):
    store.log_followup("Acme Traders", "call", "x", "a", promise_date=D0 + timedelta(days=3))
    store.resolve_promise(1, "cancelled", "a")
    assert store.reliability(D0 + timedelta(days=30)) == {}


def test_suggests_kept_when_enough_payment_arrives_after_the_promise(store, clock):
    store.log_followup("Acme Traders", "call", "x", "a", promise_date=D0 + timedelta(days=5), promise_amount=Decimal("30000"))
    assert store.promise_suggestions() == []                                       # nothing paid yet
    clock.tick(2)
    store.apply_import(rows([*A1[:5], "20000"], A2, B1), actor="t")                # +20,000: part
    [s] = store.promise_suggestions()
    assert (s.kind, s.observed) == ("partial", Decimal("20000.00"))
    clock.tick(1)
    store.apply_import(rows([*A1[:5], "35000"], A2, B1), actor="t")                # now 35,000 since the promise
    [s] = store.promise_suggestions()
    assert (s.kind, s.observed) == ("looks_kept", Decimal("35000.00"))
    store.resolve_promise(1, "kept", "a")
    assert store.promise_suggestions() == []


def test_payments_before_the_promise_do_not_count_and_other_customers_are_ignored(store, clock):
    store.apply_import(rows([*A1[:5], "10000"], A2, B1), actor="t")                # payment BEFORE the promise
    clock.tick(1)
    store.log_followup("Acme Traders", "call", "x", "a", promise_date=D0 + timedelta(days=9))
    clock.tick(1)
    store.apply_import(rows([*A1[:5], "10000"], A2, ["B1", "Beta Co", "01-08-2026", "01-09-2026", "20000", "20000"]), actor="t")  # Beta pays
    assert store.promise_suggestions() == []


def test_settlement_counts_toward_a_suggestion(store, clock):
    store.log_followup("Beta Co", "call", "x", "a", promise_date=D0 + timedelta(days=5), promise_amount=Decimal("15000"))
    clock.tick(1)
    store.settle_invoices([("B1", "Beta Co")], "a")
    assert store.promise_suggestions()[0].kind == "looks_kept"


# ---- read-only import diff ----------------------------------------------------------------------------


def test_diff_import_reports_new_changed_missing_and_payments_without_writing(store):
    before = [i for i in store.invoices()]
    report = rows([*A1[:5], "25000"],                                             # paid +25,000
                  ["C1", "Cee Ltd", "01-09-2026", "01-10-2026", "7000", "0"],     # new
                  ["B1", "Beta Co", "01-08-2026", "01-09-2026", "20000", "2000"]) # reversal (5000 -> 2000)
    diff = store.diff_import(report)
    assert [n[0] for n in diff.new] == ["C1"]
    assert {c.invoice_no: c.paid_delta for c in diff.changed} == {"A1": Decimal("25000.00"), "B1": Decimal("-3000.00")}
    assert diff.payments_seen == Decimal("25000.00") and [r.invoice_no for r in diff.reversals] == ["B1"]
    assert diff.missing == [("A2", "Acme Traders", Decimal("40000.00"))]          # known, unpaid, absent from the file
    assert diff.unchanged == 0
    assert store.invoices() == before and len(store.audit_entries()) == 3         # nothing was written


def test_diff_matches_what_apply_then_does(store):
    report = rows(A1, A2, ["B1", "Beta Co", "01-08-2026", "01-09-2026", "20000", "9000"])
    diff = store.diff_import(report)
    result = store.apply_import(report, actor="t")
    assert (len(diff.new), len(diff.changed), diff.unchanged) == (result.inserted, result.updated, result.unchanged)


def test_settled_invoices_are_not_reported_missing(store):
    store.settle_invoices([("A1", "Acme Traders")], "a")
    assert [m[0] for m in store.diff_import(rows(A2, B1)).missing] == []
    assert store.apply_import(rows(A2, B1), actor="t").missing_from_import == []


# ---- activity feed ----------------------------------------------------------------------------------------


def test_activity_feed_is_newest_first_labelled_and_paged(store):
    store.log_followup("Acme Traders", "call", "x", "priya", promise_date=D0 + timedelta(days=3))
    store.open_dispute("A1", "Acme Traders", "price", "priya")
    store.update_customer("Beta Co", "priya", phone="9876543210")
    feed = store.activity(limit=100)
    assert feed[0]["action"] == "customer.updated" and feed[0]["label"] == "Beta Co"
    assert {e["action"] for e in feed} >= {"invoice.created", "followup.logged", "promise.created", "dispute.opened"}
    created = next(e for e in feed if e["action"] == "invoice.created")
    assert "·" in created["label"]
    page = store.activity(limit=3)
    older = store.activity(limit=3, before_id=page[-1]["id"])
    assert len(page) == 3 and older and older[0]["id"] < page[-1]["id"]
    assert len(store.activity(limit=10_000)) <= 500


def test_v3_database_migrates_to_v4(tmp_path):
    db = tmp_path / "v3.db"
    raw = sqlite3.connect(db)
    raw.executescript(store_module._SCHEMA_V1 + store_module._SCHEMA_V2 + store_module._SCHEMA_V3)
    raw.execute("PRAGMA user_version = 3")
    raw.commit(); raw.close()
    with Store(db, now=Clock()) as s:
        assert s._db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert s.customer_profiles() == {} and s.disputes() == [] and s.get_setting("default_country_code") == "91"
