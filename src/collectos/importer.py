"""Import pipeline: raw spreadsheet rows to validated invoices plus an honest report.

Principles: one bad row never aborts the file; nothing is silently dropped, merged
or guessed; every accepted invoice keeps a pointer to its source row.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .dates import DateError, parse_date
from .models import Invoice, InvoiceError
from .money import MoneyError, to_money
from .readers import RawRows, read_rows

# Normalised header (lowercase, alphanumerics only) -> canonical field.
# Tally-style names are included; confirm against a real export before relying on them.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "invoice_no": ("invoiceno", "invoicenumber", "invoice", "refno", "refnumber", "billno", "billref", "reference", "voucherno"),
    "customer": ("customer", "customername", "party", "partyname", "partysname", "ledger", "debtor"),
    "invoice_date": ("invoicedate", "date", "billdate", "vchdate", "voucherdate"),
    "due_date": ("duedate", "dueon", "due", "duedt"),
    "amount": ("amount", "invoiceamount", "total", "billamount", "grosstotal", "openingamount"),
    "paid": ("paid", "amountpaid", "paidamount", "received", "receivedamount"),
    "outstanding": ("outstanding", "outstandingamount", "pendingamount", "pending", "balance", "balanceamount"),
}
_LOOKUP = {alias: name for name, aliases in FIELD_ALIASES.items() for alias in aliases}
_HEADER_SCAN_ROWS = 25


class ImportFileError(ValueError):
    """The file as a whole cannot be imported (no header, missing columns, ...)."""


@dataclass(frozen=True)
class RowIssue:
    row_no: int
    message: str
    raw: dict[str, object]


@dataclass(frozen=True)
class ImportedInvoice:
    invoice: Invoice
    row_no: int
    raw: dict[str, object]


@dataclass
class ImportReport:
    source: str
    invoices: list[ImportedInvoice] = field(default_factory=list)
    errors: list[RowIssue] = field(default_factory=list)
    duplicates: list[RowIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped_rows: list[int] = field(default_factory=list)
    filled_due_dates: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def import_invoices(path: str | Path, default_credit_days: int | None = None) -> ImportReport:
    """Read a CSV/XLSX file and return validated invoices and a full issue report.

    default_credit_days, if given, fills *missing* due dates as invoice_date + N days.
    Each filled invoice number is listed in the report; nothing is filled otherwise.
    """
    path = Path(path)
    return import_rows(read_rows(path), source=path.name, default_credit_days=default_credit_days)


def import_rows(rows: RawRows, source: str = "<memory>", default_credit_days: int | None = None) -> ImportReport:
    header_idx, cols, headers = _find_header(rows)
    report = ImportReport(source=source)

    if "due_date" not in cols:
        report.warnings.append("no due-date column found; invoices will have no due date")
    if "amount" not in cols and "outstanding" in cols:
        report.warnings.append(
            "only an outstanding/pending column was found: amount is assumed equal to it and "
            "partial payments cannot be detected"
        )

    parsed: list[ImportedInvoice] = []
    for row_no, cells in rows[header_idx + 1 :]:
        raw = {headers[i]: cells[i] for i in range(min(len(headers), len(cells))) if headers[i]}
        if all(_blank(c) for c in cells):
            report.skipped_rows.append(row_no)
            continue
        if _is_totals_row(cells, cols):
            report.skipped_rows.append(row_no)
            continue
        try:
            invoice = _build_invoice(cells, cols)
        except (MoneyError, DateError, InvoiceError, ValueError) as exc:
            report.errors.append(RowIssue(row_no, str(exc), raw))
            continue
        if invoice.due_date is None and default_credit_days is not None:
            invoice = invoice.with_credit_days(default_credit_days)
            report.filled_due_dates.append(invoice.invoice_no)
        parsed.append(ImportedInvoice(invoice, row_no, raw))

    _resolve_duplicates(parsed, report)

    missing_due = [i.invoice.invoice_no for i in report.invoices if i.invoice.due_date is None]
    if missing_due:
        report.warnings.append(f"{len(missing_due)} invoice(s) have no due date and cannot be aged")
    return report


def _find_header(rows: RawRows) -> tuple[int, dict[str, int], list[str]]:
    for idx, (_, cells) in enumerate(rows[:_HEADER_SCAN_ROWS]):
        cols: dict[str, int] = {}
        clashes: list[str] = []
        for i, cell in enumerate(cells):
            name = _LOOKUP.get(_norm(cell))
            if name is None:
                continue
            if name in cols:
                clashes.append(f"{name}: columns {cols[name] + 1} and {i + 1}")
            else:
                cols[name] = i
        if _looks_like_header(cols):
            if clashes:
                raise ImportFileError("ambiguous columns (two headers map to one field): " + "; ".join(clashes))
            return idx, cols, [str(c).strip() if c is not None else "" for c in cells]
    raise ImportFileError(
        "could not find a header row. Need columns for invoice number, customer, invoice date and "
        "amount or outstanding. Accepted names: "
        + "; ".join(f"{k}: {', '.join(v)}" for k, v in FIELD_ALIASES.items())
    )


def _looks_like_header(cols: dict[str, int]) -> bool:
    return (
        {"invoice_no", "customer", "invoice_date"} <= cols.keys()
        and ("amount" in cols or "outstanding" in cols)
    )


def _build_invoice(cells: list[object], cols: dict[str, int]) -> Invoice:
    invoice_no = _text(_cell(cells, cols, "invoice_no"))
    customer = _text(_cell(cells, cols, "customer"))
    invoice_date = parse_date(_required(cells, cols, "invoice_date", "invoice date"))
    due_raw = _cell(cells, cols, "due_date")
    due_date = None if _blank(due_raw) else parse_date(due_raw)

    amount, paid = _resolve_amounts(cells, cols)
    return Invoice(invoice_no, customer, invoice_date, due_date, amount, paid)


def _resolve_amounts(cells: list[object], cols: dict[str, int]) -> tuple[Decimal, Decimal]:
    amount = _money_or_none(cells, cols, "amount")
    paid = _money_or_none(cells, cols, "paid")
    outstanding = _money_or_none(cells, cols, "outstanding")

    if amount is None:
        if outstanding is None:
            raise ValueError("amount/outstanding is missing")
        return outstanding, Decimal("0.00")
    if paid is None and outstanding is not None:
        paid = amount - outstanding
        if paid < 0:
            raise ValueError(f"outstanding {outstanding} exceeds amount {amount}")
    paid = paid if paid is not None else Decimal("0.00")
    if outstanding is not None and amount - paid != outstanding:
        raise ValueError(f"amount {amount} - paid {paid} does not equal outstanding {outstanding}")
    return amount, paid


def _resolve_duplicates(parsed: list[ImportedInvoice], report: ImportReport) -> None:
    groups: dict[tuple[str, str], list[ImportedInvoice]] = {}
    for item in parsed:
        key = (item.invoice.invoice_no, item.invoice.customer.casefold())
        groups.setdefault(key, []).append(item)

    for key, items in groups.items():
        first = items[0]
        if len(items) == 1:
            report.invoices.append(first)
        elif all(i.invoice == first.invoice for i in items):
            report.invoices.append(first)
            for extra in items[1:]:
                report.duplicates.append(
                    RowIssue(extra.row_no, f"exact duplicate of row {first.row_no} (invoice {key[0]})", extra.raw)
                )
        else:
            rows = ", ".join(str(i.row_no) for i in items)
            for item in items:
                report.errors.append(
                    RowIssue(item.row_no, f"conflicting duplicate invoice {key[0]} for same customer (rows {rows})", item.raw)
                )
    report.invoices.sort(key=lambda i: i.row_no)
    report.errors.sort(key=lambda e: e.row_no)


_TOTAL_LABEL = re.compile(r"(?i)^(grand\s+)?(total|totals|sub\s*total)\b")


def _is_totals_row(cells: list[object], cols: dict[str, int]) -> bool:
    """A footer row: no invoice number or date, and a blank or 'Total'-style party label."""
    if not (_blank(_cell(cells, cols, "invoice_no")) and _blank(_cell(cells, cols, "invoice_date"))):
        return False
    customer = _text(_cell(cells, cols, "customer"))
    return not customer or bool(_TOTAL_LABEL.match(customer))


def _cell(cells: list[object], cols: dict[str, int], name: str) -> object:
    idx = cols.get(name)
    return cells[idx] if idx is not None and idx < len(cells) else None


def _required(cells, cols, name: str, label: str) -> object:
    value = _cell(cells, cols, name)
    if _blank(value):
        raise ValueError(f"{label} is missing")
    return value


def _money_or_none(cells, cols, name: str) -> Decimal | None:
    value = _cell(cells, cols, name)
    if _blank(value):
        return None
    if isinstance(value, float):
        # Excel stores numbers as floats; accept only if exactly representable in paise
        # via its shortest repr, otherwise refuse rather than round.
        value = repr(value)
    return to_money(value)


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"\s+", " ", str(value)).strip()


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower()) if value is not None else ""
