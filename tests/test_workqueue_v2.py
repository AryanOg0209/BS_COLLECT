from datetime import date
from decimal import Decimal

import pytest

from collectos.models import Invoice, Promise, customer_key
from collectos.store_extras import Reliability
from collectos.workqueue import STAGES, QueueRules, build_queue

TODAY = date(2026, 10, 15)


def days_ago(n):
    return date.fromordinal(TODAY.toordinal() - n)


def inv(no, customer, overdue_days, amount="10000", paid="0"):
    return Invoice(no, customer, date(2025, 1, 1), days_ago(overdue_days), Decimal(amount), Decimal(paid))


def promise(customer, when, pid=1):
    return Promise(pid, customer, customer_key(customer), when, None, "open")


def queue(invoices, promises=(), last=None, **kw):
    return build_queue(invoices, TODAY, last or {}, promises, **kw)


def only(q):
    assert len(q.work) == 1
    return q.work[0]


# ---- escalation ladder --------------------------------------------------------------------


@pytest.mark.parametrize("days, amount, stage", [
    (5, "5000", 1), (30, "5000", 1), (31, "5000", 2), (60, "5000", 2), (61, "5000", 3), (90, "5000", 3), (91, "5000", 4),
    (5, "199999", 1), (5, "200000", 3),
])
def test_stage_by_age_and_size(days, amount, stage):
    item = only(queue([inv("A", "Acme", days, amount)]))
    assert item.stage == stage and item.stage_label == STAGES[stage] and item.stage_why


def test_broken_promises_escalate_the_stage():
    past = date(2026, 10, 10)
    one = only(queue([inv("A", "Acme", 5)], [promise("Acme", past)]))
    assert one.stage == 2 and "promise was broken" in one.stage_why
    two = only(queue([inv("A", "Acme", 5)], [promise("Acme", past, 1), promise("Acme", date(2026, 10, 11), 2)]))
    assert two.stage == 3 and "2 broken promises" in two.stage_why
    three = only(queue([inv("A", "Acme", 5)], [promise("Acme", date(2026, 10, d), d) for d in (9, 10, 11)]))
    assert three.stage == 4


def test_past_resolved_broken_promises_also_count_toward_the_stage():
    item = only(queue([inv("A", "Acme", 5)], reliability={"acme": Reliability(kept=0, broken=3)}))
    assert item.stage == 4 and item.promises_broken == 3


# ---- promise reliability -----------------------------------------------------------------------


def test_unreliable_history_adds_a_visible_reason():
    base = only(queue([inv("A", "Acme", 40)]))
    worse = only(queue([inv("A", "Acme", 40)], reliability={"acme": Reliability(kept=1, broken=2)}))
    assert worse.score - base.score == 10
    assert "kept 1 of 3 past promises: +10" in worse.reasons


@pytest.mark.parametrize("rel", [Reliability(2, 1), Reliability(0, 1), Reliability(5, 0)])
def test_good_or_thin_history_adds_nothing(rel):
    item = only(queue([inv("A", "Acme", 40)], reliability={"acme": rel}))
    assert not any("past promises" in r for r in item.reasons)


def test_half_broken_counts_as_unreliable():
    item = only(queue([inv("A", "Acme", 40)], reliability={"acme": Reliability(1, 1)}))
    assert any("kept 1 of 2 past promises" in r for r in item.reasons)


# ---- disputes ------------------------------------------------------------------------------------


def test_disputed_invoices_are_not_chased_but_the_rest_still_are():
    q = queue([inv("A1", "Acme", 100, "50000"), inv("A2", "Acme", 20, "8000")], disputed={("A1", "acme")})
    item = only(q)
    assert item.overdue == Decimal("8000.00") and item.max_days_overdue == 20     # the 100-day invoice is out of the chase
    assert item.disputed_overdue == Decimal("50000.00")
    assert item.total_outstanding == Decimal("58000.00")                         # exposure still counts everything
    assert q.disputed[0].customer == "Acme" and q.disputed[0].overdue == Decimal("50000.00")


def test_customer_with_only_disputed_overdue_leaves_the_queue():
    q = queue([inv("A1", "Acme", 100, "50000")], disputed={("A1", "acme")})
    assert q.work == [] and [d.customer for d in q.disputed] == ["Acme"]
    assert "resolve the dispute" in q.disputed[0].reason


def test_dispute_on_an_invoice_not_yet_due_is_not_listed_as_overdue_dispute():
    q = queue([inv("A1", "Acme", -10, "50000")], disputed={("A1", "acme")})
    assert q.work == [] and q.disputed == []


def test_dispute_only_applies_to_that_invoice_number_and_customer():
    q = queue([inv("A1", "Acme", 40, "5000"), inv("A1", "Beta", 40, "6000")], disputed={("A1", "acme")})
    assert [i.customer for i in q.work] == ["Beta"]


# ---- do not contact ---------------------------------------------------------------------------------


def test_do_not_contact_customers_wait_with_a_reason():
    q = queue([inv("A", "Acme", 100, "90000"), inv("B", "Beta", 100, "1000")], do_not_contact={"acme"})
    assert [i.customer for i in q.work] == ["Beta"]
    assert q.waiting[0].customer == "Acme" and "do-not-contact" in q.waiting[0].reason


# ---- credit limits -------------------------------------------------------------------------------------


def test_over_credit_limit_adds_points_and_escalates():
    inside = only(queue([inv("A", "Acme", 20, "10000")], credit_limits={"acme": Decimal("10000")}))        # equal is not over
    assert not inside.over_limit and inside.stage == 1
    over = only(queue([inv("A", "Acme", 20, "10000")], credit_limits={"acme": Decimal("9999")}))
    assert over.over_limit and over.stage == 3 and over.stage_why == "over credit limit"
    assert over.score - inside.score == 10
    assert any(r.startswith("over credit limit (₹10,000.00 of ₹9,999.00)") for r in over.reasons)


def test_over_limit_and_old_goes_to_review_credit():
    item = only(queue([inv("A", "Acme", 70, "10000")], credit_limits={"acme": Decimal("5000")}))
    assert item.stage == 4


def test_exposure_includes_not_yet_due_invoices():
    item = only(queue([inv("A", "Acme", 20, "6000"), inv("B", "Acme", -30, "6000")], credit_limits={"acme": Decimal("10000")}))
    assert item.total_outstanding == Decimal("12000.00") and item.over_limit


def test_rules_can_be_tuned():
    item = only(queue([inv("A", "Acme", 40)], rules=QueueRules(owner_call_days=30)))
    assert item.stage == 3
