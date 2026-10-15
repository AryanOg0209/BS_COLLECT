from datetime import date, datetime
from decimal import Decimal

import pytest
from openpyxl import Workbook

from collectos.dates import DateError, parse_date
from collectos.importer import ImportFileError, import_invoices
from collectos.readers import UnsupportedFile


def write_csv(tmp_path, text, name="in.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


HEADER = "Invoice No,Customer,Invoice Date,Due Date,Amount,Paid\n"


def test_happy_path_csv(tmp_path):
    path = write_csv(tmp_path, HEADER + "A1,Acme Traders,01-09-2026,01-10-2026,\"1,00,000.50\",25000\n")
    report = import_invoices(path)
    assert report.ok and len(report.invoices) == 1
    inv = report.invoices[0].invoice
    assert (inv.invoice_no, inv.customer) == ("A1", "Acme Traders")
    assert inv.amount == Decimal("100000.50") and inv.paid == Decimal("25000.00")
    assert inv.due_date == date(2026, 10, 1)
    assert report.invoices[0].row_no == 2


def test_tally_style_export_with_title_rows_and_totals(tmp_path):
    text = (
        "Bills Receivable,,,,,\n"
        "Demo Company,,,,,\n"
        ",,,,,\n"
        "Date,Ref. No.,Party's Name,Pending Amount,Due on,Overdue by days\n"
        "1-Sep-2026,INV-10,Sharma Steel,\"₹ 54,000.00\",1-Oct-2026,14\n"
        "5-Sep-26,INV-11,Gupta Agencies,\"12,500.00\",,\n"
        ",,Grand Total,\"66,500.00\",,\n"
    )
    report = import_invoices(write_csv(tmp_path, text))
    assert report.ok
    assert [i.invoice.invoice_no for i in report.invoices] == ["INV-10", "INV-11"]
    assert report.invoices[0].invoice.amount == Decimal("54000.00")
    assert report.invoices[1].invoice.due_date is None
    assert report.skipped_rows == [7]  # the "Grand Total" footer; title rows precede the header
    assert any("only an outstanding" in w for w in report.warnings)
    assert any("no due date" in w for w in report.warnings)


def test_bad_rows_are_reported_not_fatal(tmp_path):
    text = HEADER + (
        "G1,Good Co,01-09-2026,01-10-2026,100,0\n"
        "B1,Bad Amount,01-09-2026,01-10-2026,abc,0\n"
        "B2,Bad Date,31-02-2026,01-10-2026,100,0\n"
        "B3,Float Noise,01-09-2026,01-10-2026,0.30000000000000004,0\n"
        "B4,,01-09-2026,01-10-2026,100,0\n"
        "B5,Neg,01-09-2026,01-10-2026,-5,0\n"
        "B6,Due Before,01-09-2026,01-08-2026,100,0\n"
        "G2,Also Good,02-09-2026,02-10-2026,200,50\n"
    )
    report = import_invoices(write_csv(tmp_path, text))
    assert [i.invoice.invoice_no for i in report.invoices] == ["G1", "G2"]
    assert [e.row_no for e in report.errors] == [3, 4, 5, 6, 7, 8]
    assert not report.ok


def test_missing_due_date_warns_and_can_be_filled_explicitly(tmp_path):
    path = write_csv(tmp_path, HEADER + "A1,Acme,01-09-2026,,100,0\n")
    plain = import_invoices(path)
    assert plain.invoices[0].invoice.due_date is None
    assert any("no due date" in w for w in plain.warnings)

    filled = import_invoices(path, default_credit_days=30)
    assert filled.invoices[0].invoice.due_date == date(2026, 10, 1)
    assert filled.filled_due_dates == ["A1"]


def test_exact_duplicate_rows_are_flagged_and_not_double_counted(tmp_path):
    row = "A1,Acme,01-09-2026,01-10-2026,100,0\n"
    report = import_invoices(write_csv(tmp_path, HEADER + row + row))
    assert len(report.invoices) == 1
    assert len(report.duplicates) == 1 and report.duplicates[0].row_no == 3
    assert report.ok


def test_conflicting_duplicates_reject_all_copies(tmp_path):
    text = HEADER + "A1,Acme,01-09-2026,01-10-2026,100,0\nA1,ACME,01-09-2026,01-10-2026,150,0\n"
    report = import_invoices(write_csv(tmp_path, text))
    assert report.invoices == []
    assert [e.row_no for e in report.errors] == [2, 3]


def test_same_invoice_number_for_different_customers_is_allowed(tmp_path):
    text = HEADER + "1,Acme,01-09-2026,01-10-2026,100,0\n1,Beta,01-09-2026,01-10-2026,100,0\n"
    assert len(import_invoices(write_csv(tmp_path, text)).invoices) == 2


def test_repeated_import_is_deterministic(tmp_path):
    path = write_csv(tmp_path, HEADER + "A1,Acme,01-09-2026,01-10-2026,100,0\nA2,Acme,02-09-2026,02-10-2026,200,0\n")
    first, second = import_invoices(path), import_invoices(path)
    assert [i.invoice for i in first.invoices] == [i.invoice for i in second.invoices]


def test_amount_and_outstanding_derive_paid(tmp_path):
    text = "Invoice No,Customer,Invoice Date,Due Date,Amount,Outstanding\nA1,Acme,01-09-2026,01-10-2026,1000,400\n"
    inv = import_invoices(write_csv(tmp_path, text)).invoices[0].invoice
    assert inv.paid == Decimal("600.00") and inv.outstanding == Decimal("400.00")


def test_inconsistent_amount_paid_outstanding_is_an_error(tmp_path):
    text = "Invoice No,Customer,Invoice Date,Amount,Paid,Outstanding\nA1,Acme,01-09-2026,1000,100,500\n"
    report = import_invoices(write_csv(tmp_path, text))
    assert report.invoices == [] and "does not equal" in report.errors[0].message


def test_outstanding_greater_than_amount_is_an_error(tmp_path):
    text = "Invoice No,Customer,Invoice Date,Amount,Outstanding\nA1,Acme,01-09-2026,1000,1500\n"
    assert "exceeds" in import_invoices(write_csv(tmp_path, text)).errors[0].message


def test_wrong_columns_give_a_helpful_file_error(tmp_path):
    with pytest.raises(ImportFileError, match="could not find a header row"):
        import_invoices(write_csv(tmp_path, "foo,bar\n1,2\n"))


def test_two_headers_for_one_field_is_ambiguous(tmp_path):
    text = "Invoice No,Customer,Invoice Date,Amount,Total\nA1,Acme,01-09-2026,100,100\n"
    with pytest.raises(ImportFileError, match="ambiguous"):
        import_invoices(write_csv(tmp_path, text))


def test_unsupported_extension(tmp_path):
    path = tmp_path / "x.xls"
    path.write_bytes(b"")
    with pytest.raises(UnsupportedFile):
        import_invoices(path)


def test_xlsx_with_native_dates_and_numbers(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.append(["Invoice No", "Customer", "Invoice Date", "Due Date", "Amount", "Paid"])
    ws.append([1001, "Acme  Traders", datetime(2026, 9, 1), datetime(2026, 10, 1), 99.99, 0])
    ws.append(["1002", "Beta", "02-09-2026", "02-10-2026", 250, 100.5])
    path = tmp_path / "in.xlsx"
    wb.save(path)

    report = import_invoices(path)
    assert report.ok
    first, second = (i.invoice for i in report.invoices)
    assert first.invoice_no == "1001" and first.customer == "Acme Traders"
    assert first.amount == Decimal("99.99")
    assert second.paid == Decimal("100.50")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("15-10-2026", date(2026, 10, 15)),
        ("15/10/2026", date(2026, 10, 15)),
        ("15.10.2026", date(2026, 10, 15)),
        ("15-Oct-2026", date(2026, 10, 15)),
        ("15-Oct-26", date(2026, 10, 15)),
        ("2026-10-15", date(2026, 10, 15)),
        ("01/02/2026", date(2026, 2, 1)),  # day-first, not US month-first
    ],
)
def test_date_formats(text, expected):
    assert parse_date(text) == expected


@pytest.mark.parametrize("bad", ["", "tomorrow", "32-01-2026", "15-10-1899", 46000, None])
def test_bad_dates(bad):
    with pytest.raises(DateError):
        parse_date(bad)
