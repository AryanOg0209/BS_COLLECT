"""Dormant customers, groups, data health, the GST 180-day flag, the MSMED estimate, and cheques in the cash calendar."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from collectos.cashcal import cash_calendar
from collectos.insights import dormant_customers, group_overdue_elsewhere, group_summary, health_report
from collectos.interest import msmed_estimate
from collectos.itc import itc_flags, itc_note, itc_state
from collectos.models import Invoice, customer_key
from collectos.shipcheck import CARE, SHIP, ship_check
from collectos.store_extras import CustomerProfile
from collectos.store_registers import Cheque

T = date(2026, 10, 15)
D = Decimal


def inv(no, customer, invoice_ago=100, due_in=-10, amount="10000", paid="0"):
    return Invoice(no, customer, T - timedelta(days=invoice_ago), None if due_in is None else T + timedelta(days=due_in), D(amount), D(paid))


def on(y, m, d):
    return date(y, m, d)


# ---- dormant customers --------------------------------------------------------------------------------------------------------

def test_a_regular_customer_who_stopped_is_flagged_with_what_they_used_to_bring():
    quiet = [Invoice(f"Q-{n}", "Quiet Co", on(2026, m, 1), on(2026, m, 28), D("1000")) for n, m in enumerate((3, 4, 5, 6))]
    active = [Invoice(f"A-{n}", "Active Co", on(2026, m, 1), on(2026, m, 28), D("5000")) for n, m in enumerate((7, 8, 9, 10))]
    two = [Invoice(f"T-{n}", "Twice Co", on(2026, m, 1), on(2026, m, 28), D("9000")) for n, m in enumerate((3, 4))]
    burst = [Invoice(f"B-{n}", "Burst Co", on(2026, 9, d), on(2026, 10, d), D("100")) for n, d in enumerate((1, 10, 20))]
    rows = dormant_customers(quiet + active + two + burst, T)
    assert [r["customer"] for r in rows] == ["Quiet Co"]
    r = rows[0]
    assert r["last_invoice"] == "2026-06-01" and r["days_quiet"] == 136 and r["usual_gap_days"] == 31 and r["orders"] == 4
    assert 950 < float(r["monthly_billing"]) < 1010 and r["open_balance"] == "4000.00"


def test_dormant_sorts_by_what_they_used_to_bring_and_old_history_does_not_count():
    big = [Invoice(f"G-{n}", "Big", on(2026, m, 1), None, D("50000")) for n, m in enumerate((1, 2, 3))]
    small = [Invoice(f"S-{n}", "Small", on(2026, m, 1), None, D("500")) for n, m in enumerate((1, 2, 3))]
    ancient = [Invoice(f"O-{n}", "Ancient", on(2024, m, 1), None, D("999")) for n, m in enumerate((1, 2, 3))]
    assert [r["customer"] for r in dormant_customers(big + small + ancient, T)] == ["Big", "Small"]


# ---- groups -------------------------------------------------------------------------------------------------------------------

def profiles(**groups):
    return {customer_key(n): CustomerProfile(customer_key(n), n, credit_limit=lim, group=g) for n, (g, lim) in groups.items()}


def test_group_totals_limits_and_over_limit():
    invoices = [inv("1", "Gupta Steel", amount="80000"), inv("2", "Gupta Traders", amount="50000"), inv("3", "Gupta Traders", due_in=5, amount="30000"), inv("4", "Solo Ltd", amount="999")]
    prof = profiles(**{"Gupta Steel": ("Gupta Group", D("100000")), "Gupta Traders": ("Gupta Group", D("50000")), "Solo Ltd": ("", None)})
    out = group_summary(invoices, prof, T)
    g = out["groups"][0]
    assert out["ungrouped"] == 1 and g["group"] == "Gupta Group" and [m["customer"] for m in g["members"]] == ["Gupta Steel", "Gupta Traders"]
    assert (g["outstanding"], g["overdue"], g["combined_limit"], g["over_limit"]) == ("160000.00", "130000.00", "150000.00", True)
    assert g["members"][1]["outstanding"] == "80000.00" and g["members"][1]["overdue"] == "50000.00"


def test_group_without_limits_and_group_overdue_elsewhere():
    invoices = [inv("1", "A Co", amount="100"), inv("2", "B Co", amount="200"), inv("3", "C Co", amount="300")]
    prof = profiles(**{"A Co": ("Fam", None), "B Co": ("Fam", None), "C Co": ("Other", None)})
    g = group_summary(invoices, prof, T)["groups"]
    assert {x["group"]: x["combined_limit"] for x in g} == {"Fam": None, "Other": None} and not any(x["over_limit"] for x in g)
    assert group_overdue_elsewhere(invoices, prof, "a co", T) == ("Fam", D("200.00"), ["B Co"])
    assert group_overdue_elsewhere(invoices, prof, "C Co", T) is None                 # alone in its group
    assert group_overdue_elsewhere(invoices, profiles(**{"A Co": ("", None)}), "A Co", T) is None


# ---- data health --------------------------------------------------------------------------------------------------------------

def test_health_score_is_explained_and_exact():
    clean = health_report([inv("1", "Acme", due_in=5)], {customer_key("Acme"): CustomerProfile("acme", "Acme", phone="+919800000000")}, [], T, 2)
    assert clean["score"] == 100 and all(i["ok"] for i in clean["items"])
    bad = health_report([inv("1", "Acme", due_in=None)], {}, [], T, 30)
    assert bad["score"] == 55 and {i["key"]: i["penalty"] for i in bad["items"]}["no_due_date"] == 25.0 and {i["key"]: i["penalty"] for i in bad["items"]}["stale"] == 20.0
    assert health_report([inv("1", "Acme", due_in=None)], {}, [], T, None)["score"] == 75      # never imported: no staleness charge


def test_health_counts_missing_contacts_duplicates_and_unlimited_big_balances():
    invoices = [inv("1", "Acme", amount="200000"), inv("2", "Beta", amount="500")]
    rep = health_report(invoices, {customer_key("Beta"): CustomerProfile("beta", "Beta", email="b@x.in")}, [["Acme", "ACME Ltd"]], T, 1)
    by = {i["key"]: i for i in rep["items"]}
    assert by["no_contact"]["count"] == 1 and by["no_contact"]["penalty"] == 10.0          # Acme of the two overdue customers
    assert by["duplicate_names"]["count"] == 2 and by["no_limit"]["count"] == 1 and by["no_limit"]["penalty"] == 10.0
    assert rep["score"] == 100 - round(10.0 + by["duplicate_names"]["penalty"] + 10.0)
    assert all(i["view"] and i["fix"] and i["why"] for i in rep["items"])


def test_health_with_no_data_is_not_a_crash():
    assert health_report([], {}, [], T, None)["score"] == 100


# ---- GST 180-day flag ------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("age, state", [(149, None), (150, "approaching"), (180, "approaching"), (181, "past"), (400, "past")])
def test_itc_state_boundaries(age, state):
    assert itc_state(T - timedelta(days=age), T) == state


def test_itc_flags_only_open_invoices_oldest_first_and_the_note_lists_them():
    invoices = [inv("A", "Acme", invoice_ago=160), inv("B", "Acme", invoice_ago=200), inv("C", "Acme", invoice_ago=300, paid="10000"), inv("D", "Acme", invoice_ago=20)]
    flags = itc_flags(invoices, T)
    assert [(f["invoice_no"], f["state"]) for f in flags] == [("B", "past"), ("A", "approaching")]
    note = itc_note(flags)
    assert "Rule 37" in note and "B (200 days)" in note and "A (160 days)" in note and "accountant" in note
    assert "Rule 37" in itc_note(flags, hinglish=True) and itc_note([]) is None
    assert "and 2 more" in itc_note([{"invoice_no": str(n), "days": 160} for n in range(7)])


# ---- MSMED estimate -------------------------------------------------------------------------------------------------------------

def test_msmed_estimate_is_three_times_bank_rate_compounded_monthly():
    i = Invoice("I-1", "Acme", T - timedelta(days=100), T - timedelta(days=70), D("100000"))
    e = msmed_estimate([i], "Acme", T, D("6.5"), set())
    assert e["rate"] == "19.50" and e["rows"] == [{"invoice_no": "I-1", "outstanding": "100000.00", "delay_days": 55, "interest": "3001.17"}] and e["total"] == "3001.17"
    far = Invoice("I-2", "Acme", T - timedelta(days=200), T - timedelta(days=170), D("50000"))
    assert msmed_estimate([far], "Acme", T, D("6.5"), set())["rows"][0]["interest"] == "4343.48"
    assert "estimate" in e["note"] and "CA" in e["note"]


def test_msmed_estimate_skips_within_period_disputed_paid_and_needs_a_rate():
    ok = Invoice("I-1", "Acme", T - timedelta(days=30), T, D("1000"))
    paid = Invoice("I-3", "Acme", T - timedelta(days=300), T, D("1000"), D("1000"))
    old = Invoice("I-2", "Acme", T - timedelta(days=300), T, D("1000"))
    assert msmed_estimate([ok], "Acme", T, D("6.5"), set())["rows"] == []                   # inside the 45 days
    assert msmed_estimate([paid, old], "Acme", T, D("6.5"), {("I-2", "acme")})["rows"] == []
    assert msmed_estimate([old], "Acme", T, D("0"), set()) is None
    assert msmed_estimate([old], "Acme", T, D("6.5"), set(), appointed_days=15)["rows"][0]["delay_days"] == 285


# ---- cheques in the cash calendar ----------------------------------------------------------------------------------------------

def cheque(customer, amount, on_date, status="received", cid=1):
    return Cheque(cid, customer, customer_key(customer), "000123", "HDFC", D(amount), on_date, status, "", "", "t", "t")


def test_a_cheque_counts_on_its_date_and_never_on_top_of_the_promise_it_backs():
    invoices = [inv("A-1", "Acme", amount="50000")]
    from collectos.models import Promise
    promise = Promise(1, "Acme", "acme", T + timedelta(days=3), D("30000"), "open")
    cal = cash_calendar(invoices, [promise], {}, set(), T, cheques=[cheque("Acme", "30000", T + timedelta(days=3))])
    assert cal["totals"]["all_30"] == "50000.00"                                              # 30,000 cheque + the 20,000 left to promise, not 60,000
    assert {i["kind"] for w in cal["weeks"] for i in w["items"]} == {"cheque", "promise"}


def test_cheque_is_capped_at_what_is_owed_and_a_future_cheque_replaces_the_terms_item():
    assert cash_calendar([inv("A-1", "Acme", amount="40000")], [], {}, set(), T, cheques=[cheque("Acme", "100000", T + timedelta(days=2))])["totals"]["all_30"] == "40000.00"
    future = [inv("F-1", "Acme", due_in=20, amount="60000")]
    cal = cash_calendar(future, [], {}, set(), T, cheques=[cheque("Acme", "60000", T + timedelta(days=20))])
    assert cal["totals"]["all_30"] == "60000.00" and [i["kind"] for w in cal["weeks"] for i in w["items"]] == ["cheque"]


def test_a_cheque_dated_in_the_past_counts_today_and_final_cheques_are_ignored():
    invoices = [inv("A-1", "Acme", amount="5000")]
    cal = cash_calendar(invoices, [], {}, set(), T, cheques=[cheque("Acme", "5000", T - timedelta(days=3))])
    assert cal["weeks"][0]["items"][0]["date"] == T.isoformat() and cal["weeks"][0]["total"] == "5000.00"
    ignored = [cheque("Acme", "5000", T + timedelta(days=2), "cleared", 1), cheque("Acme", "5000", T + timedelta(days=2), "returned", 2)]
    assert cash_calendar(invoices, [], {}, set(), T, cheques=ignored)["totals"]["all_30"] == "0.00"


def test_a_bounced_cheque_makes_the_next_one_shaky_and_cheque_money_is_not_undated():
    invoices = [inv("A-1", "Acme", amount="9000")]
    cal = cash_calendar(invoices, [], {}, set(), T, cheques=[cheque("Acme", "4000", T + timedelta(days=1), "bounced", 1), cheque("Acme", "4000", T + timedelta(days=2), "received", 2)])
    assert cal["weeks"][0]["shaky"] == "4000.00" and cal["weeks"][0]["firm"] == "0.00"
    assert cal["undated"]["amount"] == "5000.00"                                              # 9,000 owed minus the 4,000 a cheque covers
    firm = cash_calendar(invoices, [], {}, set(), T, cheques=[cheque("Acme", "9000", T + timedelta(days=2))])
    assert firm["weeks"][0]["firm"] == "9000.00" and firm["undated"]["customers"] == 0


# ---- ship check: related firms --------------------------------------------------------------------------------------------------

def test_ship_check_warns_when_related_firms_owe_overdue():
    ok = [inv("A-1", "Acme", due_in=10)]
    assert ship_check(ok, "Acme", D("10"), T)["verdict"] == SHIP
    r = ship_check(ok, "Acme", D("10"), T, group_elsewhere=("Gupta Group", D("120000"), ["Gupta Steel"]))
    assert r["verdict"] == CARE and any("Gupta Group" in x["text"] and "₹1,20,000.00" in x["text"] for x in r["reasons"])
