from datetime import date
from decimal import Decimal

import pytest

from collectos.ageing import (
    CURRENT,
    NO_DUE_DATE,
    OVERPAID,
    SETTLED,
    Status,
    assess,
    bucket_labels,
    summarise_by_customer,
)
from collectos.models import Invoice, InvoiceError

TODAY = date(2026, 10, 15)


def inv(no="INV-1", customer="Acme", due=date(2026, 10, 15), amount="1000", paid="0", inv_date=date(2025, 1, 1)):
    return Invoice(no, customer, inv_date, due, Decimal(amount), Decimal(paid))


def test_not_yet_due():
    r = assess(inv(due=date(2026, 10, 16)), TODAY)
    assert (r.status, r.days_overdue, r.bucket) == (Status.NOT_DUE, 0, CURRENT)


def test_due_today_is_not_overdue():
    r = assess(inv(due=TODAY), TODAY)
    assert (r.status, r.days_overdue, r.bucket) == (Status.DUE_TODAY, 0, CURRENT)


def test_one_day_overdue():
    r = assess(inv(due=date(2026, 10, 14)), TODAY)
    assert (r.status, r.days_overdue, r.bucket) == (Status.OVERDUE, 1, "1-30")


@pytest.mark.parametrize(
    "days, bucket",
    [(30, "1-30"), (31, "31-60"), (60, "31-60"), (61, "61-90"), (90, "61-90"), (91, "91+"), (400, "91+")],
)
def test_bucket_boundaries(days, bucket):
    due = date.fromordinal(TODAY.toordinal() - days)
    assert assess(inv(due=due), TODAY).bucket == bucket


def test_custom_edges_match_tally_style_slabs():
    assert bucket_labels((45, 90, 180)) == ["1-45", "46-90", "91-180", "181+"]
    due = date.fromordinal(TODAY.toordinal() - 46)
    assert assess(inv(due=due), TODAY, edges=(45, 90, 180)).bucket == "46-90"


@pytest.mark.parametrize("edges", [(), (0, 10), (30, 30), (60, 30)])
def test_invalid_edges_rejected(edges):
    with pytest.raises(ValueError):
        bucket_labels(edges)


def test_fully_paid_is_settled_even_if_past_due():
    r = assess(inv(due=date(2026, 1, 1), paid="1000"), TODAY)
    assert (r.status, r.bucket, r.outstanding) == (Status.PAID, SETTLED, Decimal("0.00"))


def test_partially_paid_overdue_ages_the_remaining_balance():
    r = assess(inv(due=date(2026, 9, 1), amount="1000", paid="400.50"), TODAY)
    assert r.status is Status.OVERDUE
    assert r.is_partial is True
    assert r.outstanding == Decimal("599.50")


def test_partially_paid_not_due_is_flagged_partial():
    r = assess(inv(due=date(2026, 12, 1), paid="1"), TODAY)
    assert (r.status, r.is_partial) == (Status.NOT_DUE, True)


def test_overpaid_is_surfaced_not_hidden():
    r = assess(inv(paid="1200"), TODAY)
    assert (r.status, r.bucket, r.outstanding) == (Status.OVERPAID, OVERPAID, Decimal("-200.00"))


def test_missing_due_date_is_flagged_not_guessed():
    r = assess(inv(due=None), TODAY)
    assert (r.status, r.bucket) == (Status.NO_DUE_DATE, NO_DUE_DATE)


def test_credit_days_fill_is_explicit_and_traceable():
    filled = inv(due=None, inv_date=date(2026, 9, 1)).with_credit_days(30)
    assert filled.due_date == date(2026, 10, 1)
    assert assess(filled, TODAY).days_overdue == 14


def test_decimal_precision_is_preserved_across_many_invoices():
    results = [assess(inv(no=f"I{i}", amount="0.10", due=date(2026, 9, 1)), TODAY) for i in range(10)]
    s = summarise_by_customer(results)["Acme"]
    assert s.outstanding == Decimal("1.00")


def test_multiple_invoices_one_customer_summary():
    results = [
        assess(inv("A", due=date(2026, 12, 1), amount="500"), TODAY),                 # current
        assess(inv("B", due=date(2026, 10, 5), amount="1000", paid="250"), TODAY),    # 10d, partial
        assess(inv("C", due=date(2026, 6, 1), amount="2000"), TODAY),                 # 136d
        assess(inv("D", due=date(2026, 1, 1), amount="900", paid="900"), TODAY),      # settled, excluded
        assess(inv("E", customer="Other", due=date(2026, 10, 1), amount="75"), TODAY),
    ]
    summary = summarise_by_customer(results)
    acme = summary["Acme"]
    assert acme.outstanding == Decimal("3250.00")
    assert acme.overdue == Decimal("2750.00")
    assert (acme.open_invoices, acme.overdue_invoices, acme.max_days_overdue) == (3, 2, 136)
    assert acme.by_bucket == {CURRENT: Decimal("500.00"), "1-30": Decimal("750.00"), "91+": Decimal("2000.00")}
    assert summary["Other"].overdue == Decimal("75.00")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"amount": Decimal("0")},
        {"amount": Decimal("-5")},
        {"paid": Decimal("-1")},
        {"invoice_no": "  "},
        {"customer": ""},
        {"due_date": date(2026, 8, 1)},  # before invoice_date (2026-09-01)
        {"amount": 10.5},                # float rejected
    ],
)
def test_invalid_invoices_rejected(kwargs):
    base = dict(
        invoice_no="X", customer="Acme", invoice_date=date(2026, 9, 1), due_date=date(2026, 10, 1),
        amount=Decimal("100"), paid=Decimal("0"),
    )
    base.update(kwargs)
    with pytest.raises((InvoiceError, ValueError)):
        Invoice(**base)
