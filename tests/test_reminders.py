from datetime import date
from decimal import Decimal

import pytest

from collectos.ageing import assess
from collectos.models import Invoice, Promise, customer_key
from collectos.reminders import FIRM, FRIENDLY, PROMISE, Sender, draft_reminder

TODAY = date(2026, 10, 15)
SENDER = Sender("Rohit", "Sharma Steel Pvt Ltd")


def ageing(no, days, amount="10000", paid="0", customer="Acme"):
    due = date.fromordinal(TODAY.toordinal() - days)
    inv = Invoice(no, customer, date(2025, 1, 1), due, Decimal(amount), Decimal(paid))
    return assess(inv, TODAY)


def test_friendly_template_for_recent_overdue():
    d = draft_reminder("Acme", [ageing("INV-1", 10, "54000")], SENDER, TODAY)
    assert d.template == FRIENDLY
    assert d.subject == "Payment reminder: 1 invoice, ₹54,000.00"
    assert "- INV-1: due 05-Oct-2026, ₹54,000.00 (10 days overdue)" in d.email_body
    assert d.email_body.startswith("Dear Acme team,")
    assert d.email_body.endswith("Regards,\nRohit\nSharma Steel Pvt Ltd")
    assert "UTR" in d.email_body and "UTR" in d.whatsapp_text


def test_firm_template_after_sixty_days_still_makes_no_threats():
    d = draft_reminder("Acme", [ageing("INV-1", 75)], SENDER, TODAY)
    assert d.template == FIRM
    for banned in ("legal", "court", "interest", "penalty", "notice", "blacklist", "43B"):
        assert banned not in d.email_body.lower() and banned not in d.whatsapp_text.lower()


def test_promise_template_wins_over_age_and_names_the_date_and_amount():
    p = Promise(1, "Acme", customer_key("Acme"), date(2026, 10, 10), Decimal("20000"), "open")
    d = draft_reminder("Acme", [ageing("INV-1", 90)], SENDER, TODAY, broken_promises=[p])
    assert d.template == PROMISE
    assert "payment of ₹20,000.00 was expected from you by 10-Oct-2026" in d.email_body
    assert "10-Oct-2026" in d.subject


def test_multiple_invoices_are_listed_oldest_first_with_exact_total():
    d = draft_reminder("Acme", [ageing("B", 5, "0.10"), ageing("A", 20, "0.20")], SENDER, TODAY)
    assert d.email_body.index("- A:") < d.email_body.index("- B:")
    assert "Total overdue: ₹0.30" in d.email_body
    assert d.subject == "Payment reminder: 2 invoices, ₹0.30"


def test_partial_payment_quotes_only_the_remaining_balance():
    d = draft_reminder("Acme", [ageing("INV-1", 10, amount="1000", paid="400")], SENDER, TODAY)
    assert "₹600.00" in d.email_body and "₹1,000.00" not in d.email_body


def test_long_lists_are_truncated_with_an_exact_remainder_total():
    items = [ageing(f"INV-{i:02d}", 100 - i, "100") for i in range(8)]
    d = draft_reminder("Acme", items, SENDER, TODAY)
    assert d.whatsapp_text.count("- INV-") == 5
    assert "- and 3 more invoice(s) totalling ₹300.00" in d.whatsapp_text
    assert "and 3 more" not in d.email_body  # email shows up to 15


def test_not_due_paid_and_undated_invoices_are_ignored():
    not_due = ageing("N", -5)
    paid = ageing("P", 30, amount="100", paid="100")
    with pytest.raises(ValueError, match="no overdue invoices"):
        draft_reminder("Acme", [not_due, paid], SENDER, TODAY)
    d = draft_reminder("Acme", [not_due, paid, ageing("O", 3)], SENDER, TODAY)
    assert "- O:" in d.email_body and "- N:" not in d.email_body and "- P:" not in d.email_body
