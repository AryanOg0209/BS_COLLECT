from datetime import date
from decimal import Decimal

import pytest

from collectos.matcher import Credit, StatementError, parse_statement, suggest
from collectos.models import Invoice, Promise, customer_key
from collectos.msme import past_msme_window

AS_OF = date(2026, 10, 15)


def inv(no, customer, amount, paid="0", inv_date=date(2026, 8, 1), due=date(2026, 9, 1)):
    return Invoice(no, customer, inv_date, due, Decimal(amount), Decimal(paid))


INVOICES = [
    inv("INV-1001", "Acme Traders Pvt Ltd", "50000"),
    inv("INV-1002", "Acme Traders Pvt Ltd", "30000.50", due=date(2026, 9, 10)),
    inv("INV-2001", "Beta Steel Co", "98000"),
    inv("INV-3001", "Gamma Polymers", "12345.67"),
    inv("INV-3002", "Gamma Polymers", "12000"),
    inv("INV-4001", "Delta Traders", "12000"),
]


def credit(narration, amount, n=1):
    return Credit(n, AS_OF, narration, Decimal(amount), "")


def one(narration, amount, invoices=INVOICES, promises=()):
    [s] = suggest([credit(narration, amount)], invoices, AS_OF, promises)
    return s


# ---- matching ------------------------------------------------------------------------------------


def test_invoice_number_in_narration_is_high_confidence():
    s = one("NEFT-HDFC-INV-1001-PAYMENT", "50000")
    assert (s.confidence, s.customer, s.invoice_nos) == ("high", "Acme Traders Pvt Ltd", ("INV-1001",))
    assert any("INV-1001 appears in the narration" in r for r in s.reasons) and any("equals the outstanding on INV-1001" in r for r in s.reasons)


def test_invoice_number_matching_respects_boundaries():
    s = one("PAYMENT INV10010 REF", "50000", invoices=[inv("INV-1001", "Acme Traders", "50000")])
    assert s.invoice_nos == () or s.confidence != "high"
    assert one("UPI/INV1001/ok", "50000").confidence == "high"      # joined form still matches


def test_short_invoice_numbers_are_not_used_for_matching():
    s = one("RTGS A1 PAYMENT", "777", invoices=[inv("A1", "Acme Traders", "777")])
    assert s.confidence in ("low", "medium") and not any("appears in the narration" in r for r in s.reasons)


def test_name_and_exact_amount_is_high():
    s = one("NEFT CR-ACME TRADERS PVT LTD-UTR12345", "50000")
    assert (s.confidence, s.invoice_nos) == ("high", ("INV-1001",))


def test_two_invoices_summing_to_the_amount():
    s = one("ACME TRADERS NEFT", "80000.50")
    assert s.confidence == "high" and set(s.invoice_nos) == {"INV-1001", "INV-1002"}
    assert s.matched_outstanding == Decimal("80000.50")


def test_name_only_is_medium_and_says_the_amount_does_not_match():
    s = one("IMPS BETA STEEL", "40000")
    assert (s.confidence, s.customer, s.invoice_nos) == ("medium", "Beta Steel Co", ())
    assert any("does not equal any combination" in r for r in s.reasons)


@pytest.mark.parametrize("rate, label", [("0.01", "1%"), ("0.02", "2%"), ("0.05", "5%"), ("0.10", "10%")])
def test_tds_shortfalls_are_flagged_with_the_rate(rate, label):
    amount = (Decimal("98000") * (1 - Decimal(rate))).quantize(Decimal("0.01"))
    s = one("BETA STEEL CO NEFT", str(amount))
    assert s.customer == "Beta Steel Co" and s.invoice_nos == ("INV-2001",)
    assert any(f"about {label} below" in r and "TDS" in r and "confirm" in r for r in s.reasons)
    assert s.confidence == "medium"          # never upgraded to high on a guess


def test_other_shortfalls_are_not_called_tds():
    s = one("BETA STEEL CO NEFT", str((Decimal("98000") * Decimal("0.97")).quantize(Decimal("0.01"))))
    assert not any("TDS" in r for r in s.reasons) and s.invoice_nos == ()


def test_unique_exact_amount_without_a_name_is_medium_unless_round():
    assert one("UPI RANDOM PAYER 987", "12345.67").confidence == "medium"
    s = one("UPI RANDOM PAYER 987", "98000")
    assert s.confidence == "low" and s.invoice_nos == ("INV-2001",)


def test_ambiguous_amount_identifies_nothing():
    s = one("UPI RANDOM", "12000")      # Gamma INV-3002 and Delta INV-4001 are both 12,000
    assert s.confidence == "none" and s.customer is None and any("2 different invoices" in r for r in s.reasons)


def test_partial_name_is_low():
    s = one("NEFT ACME TRADERS", "999", invoices=[inv("INV-1", "Acme Traders Private Limited Mumbai", "5000")])
    assert s.confidence in ("medium", "low")
    s2 = one("NEFT BETA", "999")
    assert s2.confidence == "low" and s2.customer == "Beta Steel Co" and any("part of the customer's name" in r for r in s2.reasons)


def test_two_customers_sharing_a_name_word_is_ambiguous():
    s = one("DELTA TRADERS ACME TRADERS", "1")
    assert s.confidence == "low" and s.customer is None


def test_no_clue_at_all():
    s = one("UPI/PHONEPE/12345", "7")
    assert (s.confidence, s.customer, s.invoice_nos) == ("none", None, ())


def test_credit_larger_than_everything_owed_mentions_a_possible_advance():
    s = one("ACME TRADERS", "500000")
    assert s.customer == "Acme Traders Pvt Ltd" and any("may include an advance" in r for r in s.reasons)


def test_open_promises_are_attached_to_the_matched_customer():
    p = Promise(7, "Acme Traders Pvt Ltd", customer_key("Acme Traders Pvt Ltd"), date(2026, 10, 20), None, "open")
    assert one("ACME TRADERS NEFT", "50000", promises=[p]).promise_ids == (7,)
    assert one("BETA STEEL", "98000", promises=[p]).promise_ids == ()


def test_settled_invoices_are_never_suggested():
    s = one("INV-1001 PAYMENT", "50000", invoices=[inv("INV-1001", "Acme Traders", "50000", paid="50000")])
    assert s.confidence == "none"


def test_every_credit_gets_a_suggestion_even_when_none_match():
    out = suggest([credit("A", "1", 1), credit("INV-1001", "50000", 2)], INVOICES, AS_OF)
    assert [s.credit.row_no for s in out] == [1, 2] and [s.confidence for s in out] == ["none", "high"]


# ---- statement parsing ------------------------------------------------------------------------------


def rows(*lines):
    return [(i, list(r)) for i, r in enumerate(lines, start=1)]


def test_parses_a_typical_indian_bank_csv_and_ignores_debits():
    r = parse_statement(rows(
        ["Account Statement"], [],
        ["Date", "Narration", "Chq./Ref.No.", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"],
        ["01-10-2026", "NEFT CR-ACME TRADERS", "N123", "", "1,50,000.00", "5,00,000.00"],
        ["02-10-2026", "ATM WDL", "A1", "2,000.00", "", "4,98,000.00"],
        ["03-10-2026", "UPI-BETA STEEL", "U9", "", "98000", "5,96,000.00"],
    ))
    assert [(c.narration, c.amount, c.reference) for c in r.credits] == [("NEFT CR-ACME TRADERS", Decimal("150000.00"), "N123"),
                                                                           ("UPI-BETA STEEL", Decimal("98000.00"), "U9")]
    assert r.skipped == 1 and r.errors == [] and r.credits[0].on == date(2026, 10, 1) and r.credits[0].row_no == 4


def test_parses_amount_plus_drcr_layout():
    r = parse_statement(rows(["Txn Date", "Description", "Amount", "Dr / Cr"], ["01/10/2026", "Receipt ACME", "5000", "CR"], ["02/10/2026", "Rent", "9000", "DR"]))
    assert [c.amount for c in r.credits] == [Decimal("5000.00")] and r.skipped == 1


def test_bad_amounts_are_reported_not_fatal():
    r = parse_statement(rows(["Date", "Narration", "Credit"], ["01-10-2026", "ok", "100"], ["02-10-2026", "bad", "12.345"], ["03-10-2026", "worse", "abc"]))
    assert len(r.credits) == 1 and len(r.errors) == 2 and "row 3" in r.errors[0]


def test_unparseable_dates_do_not_block_a_credit():
    r = parse_statement(rows(["Date", "Narration", "Credit"], ["not a date", "x", "10"]))
    assert r.credits[0].on is None


def test_wrong_file_gets_a_helpful_error():
    with pytest.raises(StatementError, match="could not find the statement columns"):
        parse_statement(rows(["Invoice No", "Customer", "Amount"], ["A1", "Acme", "5"]))


def test_float_cells_from_excel_are_handled_exactly():
    r = parse_statement(rows(["Date", "Narration", "Credit"], ["01-10-2026", "x", 1234.5]))
    assert r.credits[0].amount == Decimal("1234.50")


# ---- MSME informational flag -----------------------------------------------------------------------------


def test_msme_window_marks_only_open_invoices_older_than_45_days():
    items = [inv("A", "X", "100", inv_date=date(2026, 8, 30)),            # 46 days
             inv("B", "X", "100", inv_date=date(2026, 8, 31)),            # 45 days: not past
             inv("C", "X", "100", paid="100", inv_date=date(2026, 1, 1))]  # settled
    assert [i.invoice_no for i in past_msme_window(items, AS_OF)] == ["A"]
