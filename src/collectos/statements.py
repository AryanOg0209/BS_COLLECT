"""Statement of account and balance-confirmation letter for one customer.

"Send me the ledger" is the most common stalling reply in B2B collections, so this makes the
answer one click. It lists the open invoices held in the data (no payment history is invented),
a closing balance, and a polite request to confirm or point out differences. Drafts make no legal,
interest or tax claims. A person reviews and sends them.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from .ageing import Status, assess
from .models import Invoice, customer_key
from .money import format_inr
from .report import _HEADER_FILL, _put, _widths

MAX_LINES_TEXT = 12
_ZERO = Decimal("0.00")


def account_statement(invoices: list[Invoice], customer: str, as_of: date, sender: str = "", company: str = "", language: str = "en") -> dict:
    """The open items for one customer, with totals, a message you can send and the confirmation letter."""
    key = customer_key(customer)
    mine = [i for i in invoices if customer_key(i.customer) == key]
    if not mine:
        raise ValueError(f"unknown customer {customer!r}")
    if language not in ("en", "hinglish"):
        raise ValueError("language must be en or hinglish")
    display = mine[0].customer
    rows, total, overdue = [], _ZERO, _ZERO
    for i in sorted(mine, key=lambda i: (i.invoice_date, i.invoice_no)):
        a = assess(i, as_of)
        if a.status in (Status.PAID, Status.OVERPAID):
            continue
        late = a.status is Status.OVERDUE
        total += a.outstanding
        overdue += a.outstanding if late else _ZERO
        rows.append({"invoice_no": i.invoice_no, "invoice_date": i.invoice_date.isoformat(),
                     "due_date": None if i.due_date is None else i.due_date.isoformat(), "amount": f"{i.amount:.2f}",
                     "paid": f"{i.paid:.2f}", "balance": f"{a.outstanding:.2f}", "days_overdue": a.days_overdue if late else 0,
                     "status": a.status.value})
    hi = language == "hinglish"
    return {
        "customer": display, "as_of": as_of.isoformat(), "rows": rows, "invoice_count": len(rows),
        "balance": f"{total:.2f}", "overdue": f"{overdue:.2f}",
        "message": _message(display, rows, total, overdue, as_of, sender, company, hi),
        "confirmation": _confirmation(display, len(rows), total, as_of, sender, company, hi),
    }


def _dmy(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d-%b-%Y")


def _message(customer: str, rows: list[dict], total: Decimal, overdue: Decimal, as_of: date, sender: str, company: str, hi: bool) -> str:
    when = as_of.strftime("%d-%b-%Y")
    if not rows:
        return f"{customer}: no open invoices as on {when}."
    lines = []
    for r in rows[:MAX_LINES_TEXT]:
        due = f", due {_dmy(r['due_date'])}" if r["due_date"] else ""
        late = (f", {r['days_overdue']} din late" if hi else f", {r['days_overdue']} days overdue") if r["days_overdue"] else ""
        lines.append(f"- {r['invoice_no']} ({_dmy(r['invoice_date'])}{due}): {format_inr(Decimal(r['balance']))}{late}")
    if len(rows) > MAX_LINES_TEXT:
        lines.append(f"- ...and {len(rows) - MAX_LINES_TEXT} more (see the attached statement)" if not hi else f"- ...aur {len(rows) - MAX_LINES_TEXT} (statement dekhein)")
    sign = "\n".join(x for x in ((f"Dhanyavaad,\n{sender}" if hi else f"Regards,\n{sender}") if sender else "", company) if x)
    if hi:
        head = f"Namaste {customer} team,\n{when} tak ka aapka statement of account:"
        tail = f"Kul baaki: {format_inr(total)} (isme se overdue {format_inr(overdue)}).\nKripya apni books se milaan karke bata dein agar koi entry alag ho."
    else:
        head = f"Dear {customer} team,\nStatement of account as on {when}:"
        tail = f"Total outstanding: {format_inr(total)} (of which overdue {format_inr(overdue)}).\nPlease check against your books and tell us if any entry differs."
    return "\n".join([head, *lines, tail] + ([sign] if sign else []))


def _confirmation(customer: str, count: int, total: Decimal, as_of: date, sender: str, company: str, hi: bool) -> str:
    when = as_of.strftime("%d-%b-%Y")
    sign = "\n".join(x for x in ((f"Dhanyavaad,\n{sender}" if hi else f"Regards,\n{sender}") if sender else "", company) if x)
    inv = f"{count} invoice" + ("" if count == 1 else "s")
    if hi:
        body = (f"Namaste {customer} team,\n\nHamare records ke hisaab se {when} tak aapse {format_inr(total)} lena baaki hai ({inv}; detail statement mein hai).\n"
                "Kripya 7 din ke andar is balance ki pushti karein, ya agar fark ho to invoice number ke saath bata dein.")
    else:
        body = (f"Dear {customer} team,\n\nAs per our records, the balance receivable from you as on {when} is {format_inr(total)} ({inv}, listed in the attached statement).\n"
                "Please confirm this balance within 7 days, or tell us the difference with the invoice numbers.")
    return body + (f"\n\n{sign}" if sign else "")


def write_statement_xlsx(path: Path, data: dict, company: str = "") -> Path:
    """Two sheets: the statement, and the balance-confirmation letter with a reply slip. Text never acts as a formula."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Statement"
    bold = Font(bold=True)
    _put(ws, 1, 1, f"Statement of account: {data['customer']}", Font(bold=True, size=14))
    _put(ws, 2, 1, f"As on {_dmy(data['as_of'])}" + (f"   |   {company}" if company else ""))
    heads = ["Invoice no", "Invoice date", "Due date", "Amount", "Received", "Balance", "Days overdue"]
    for c, text in enumerate(heads, start=1):
        cell = _put(ws, 4, c, text, Font(bold=True, color="FFFFFF"))
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(wrap_text=True)
    r = 4
    for r, row in enumerate(data["rows"], start=5):
        values = [row["invoice_no"], date.fromisoformat(row["invoice_date"]), None if row["due_date"] is None else date.fromisoformat(row["due_date"]),
                  Decimal(row["amount"]), Decimal(row["paid"]), Decimal(row["balance"]), row["days_overdue"] or ""]
        for c, v in enumerate(values, start=1):
            _put(ws, r, c, v)
    end = r + 2
    _put(ws, end, 5, "Closing balance", bold)
    _put(ws, end, 6, Decimal(data["balance"]), bold)
    _put(ws, end + 1, 5, "Of which overdue")
    _put(ws, end + 1, 6, Decimal(data["overdue"]))
    ws.freeze_panes = "A5"
    _widths(ws, [16, 14, 14, 16, 16, 16, 14])

    cf = wb.create_sheet("Balance confirmation")
    for n, line in enumerate(data["confirmation"].split("\n"), start=1):
        _put(cf, n, 1, line)
    n = len(data["confirmation"].split("\n")) + 2
    for label in ("Balance confirmed as stated  [   ]", "Balance differs; our figure is: ______________  (invoice numbers: ______________)",
                  "Name and seal: ______________________", "Date: ____________"):
        _put(cf, n, 1, label)
        n += 2
    cf.column_dimensions["A"].width = 110
    wb.save(path)
    return path
