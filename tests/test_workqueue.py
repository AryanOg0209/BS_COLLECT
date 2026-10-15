from datetime import date
from decimal import Decimal

import pytest

from collectos.models import Invoice, Promise, customer_key
from collectos.money import format_inr
from collectos.workqueue import QueueRules, build_queue

TODAY = date(2026, 10, 15)


def inv(no, customer, due, amount="10000", paid="0"):
    return Invoice(no, customer, date(2025, 1, 1), due, Decimal(amount), Decimal(paid))


def promise(customer, when, pid=1, amount=None):
    return Promise(pid, customer, customer_key(customer), when, amount, "open")


def days_ago(n):
    return date.fromordinal(TODAY.toordinal() - n)


def build(invoices, last=None, promises=(), rules=QueueRules()):
    return build_queue(invoices, TODAY, last or {}, promises, rules)


@pytest.mark.parametrize(
    "value, text",
    [
        ("0", "₹0.00"), ("999.5", "₹999.50"), ("1000", "₹1,000.00"), ("250000", "₹2,50,000.00"),
        ("12345678.9", "₹1,23,45,678.90"), ("-1500", "-₹1,500.00"),
    ],
)
def test_indian_grouping(value, text):
    assert format_inr(Decimal(value)) == text


def test_customers_with_nothing_overdue_are_not_queued():
    q = build([inv("A", "Acme", date(2026, 12, 1)), inv("B", "Beta", TODAY)])
    assert q.work == [] and q.waiting == []


def test_score_is_the_sum_of_visible_reasons():
    q = build([inv("A", "Acme", days_ago(45), amount="30000")])
    [item] = q.work
    # 30,000 overdue (+20), 45 days (+10), never contacted (+10)
    assert item.score == 40
    assert item.reasons == (
        "₹30,000.00 overdue: +20",
        "oldest invoice 45 days overdue: +10",
        "never contacted: +10",
    )
    assert item.action == "Call"  # older than 30 days


def test_new_small_overdue_gets_a_reminder_not_a_call():
    [item] = build([inv("A", "Acme", days_ago(5), amount="2000")]).work
    assert item.action == "Send reminder"


def test_large_amount_forces_a_call_even_if_recent():
    [item] = build([inv("A", "Acme", days_ago(2), amount="60000")]).work
    assert item.action == "Call"


def test_ordering_by_score_then_amount():
    q = build([
        inv("A", "Small Old", days_ago(100), amount="3000"),    # 5+30+10 = 45
        inv("B", "Big Old", days_ago(100), amount="200000"),    # 30+30+10 = 70
        inv("C", "Mid New", days_ago(10), amount="30000"),      # 20+5+10  = 35
    ])
    assert [i.customer for i in q.work] == ["Big Old", "Small Old", "Mid New"]


def test_multiple_invoices_are_combined_per_customer_and_name_variants_merge():
    q = build([inv("A", "Acme  Traders", days_ago(10), amount="20000"), inv("B", "ACME TRADERS", days_ago(70), amount="10000")])
    [item] = q.work
    assert item.overdue == Decimal("30000.00") and item.max_days_overdue == 70


def test_pending_promise_moves_customer_to_waiting():
    q = build([inv("A", "Acme", days_ago(60))], promises=[promise("Acme", date(2026, 10, 20))])
    assert q.work == []
    assert q.waiting[0].reason == "promised to pay by 20-Oct-2026"


def test_promise_due_today_is_still_pending_and_tomorrow_it_is_broken():
    assert build([inv("A", "Acme", days_ago(60))], promises=[promise("Acme", TODAY)]).waiting
    q = build([inv("A", "Acme", days_ago(60))], promises=[promise("Acme", days_ago(1))])
    [item] = q.work
    assert item.broken_promises and any("broken promise" in r for r in item.reasons)
    assert item.action == "Call"


def test_broken_promise_beats_cooldown_and_pending_promise():
    q = build(
        [inv("A", "Acme", days_ago(20))],
        last={"acme": days_ago(1)},
        promises=[promise("Acme", days_ago(3), pid=1), promise("Acme", date(2026, 11, 1), pid=2)],
    )
    assert len(q.work) == 1 and q.waiting == []


def test_cooldown_after_recent_contact():
    q = build([inv("A", "Acme", days_ago(60))], last={"acme": days_ago(2)})
    assert q.work == [] and q.waiting[0].reason == "contacted 2 day(s) ago"
    assert build([inv("A", "Acme", days_ago(60))], last={"acme": days_ago(3)}).work


def test_stale_contact_adds_points():
    [fresh] = build([inv("A", "Acme", days_ago(40), amount="10000")], last={"acme": days_ago(5)}).work
    [stale] = build([inv("A", "Acme", days_ago(40), amount="10000")], last={"acme": days_ago(20)}).work
    assert stale.score - fresh.score == 10
    assert "last contact 20 days ago: +10" in stale.reasons


def test_open_promise_with_nothing_overdue_is_flagged_for_review():
    q = build([inv("A", "Acme", date(2026, 12, 1))], promises=[promise("Acme", days_ago(2))])
    assert q.work == [] and "mark it kept or cancelled" in q.promises_to_review[0].reason


def test_paid_invoices_never_appear():
    assert build([inv("A", "Acme", days_ago(60), amount="500", paid="500")]).work == []


def test_rules_are_tunable():
    rules = QueueRules(cooldown_days=0, call_if_days_over=90, never_contacted_points=0)
    [item] = build([inv("A", "Acme", days_ago(45), amount="1000")], rules=rules).work
    assert item.action == "Send reminder"
    assert item.score == 15  # 5 (amount) + 10 (age); no never-contacted points
    assert not any("never contacted" in r for r in item.reasons)
