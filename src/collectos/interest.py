"""Interest that could be claimed on overdue invoices, and a waiver offer the owner may choose to make.

Simple interest only: outstanding x rate x days past due (after any grace days) / 365. The rate is the
one *the owner states* in Settings from their own invoice or PO terms; CollectOS does not know what was
agreed. Invoices in dispute are left out. The waiver draft is text for a person to review: offering a
waiver is a commercial decision, and whether interest can be claimed at all is a question for your CA.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .ageing import Status, assess
from .models import Invoice, customer_key
from .money import format_inr

_ZERO = Decimal("0.00")
MSMED_NOTE = ("If you are a registered micro or small supplier, the MSMED Act may entitle you to interest at three times the RBI bank rate "
              "(compounded monthly) on payments delayed beyond the agreed period. Informational only; confirm with your CA before claiming.")
TERMS_NOTE = "Use this only if your invoice or purchase-order terms mention interest. CollectOS cannot know what was agreed."


@dataclass(frozen=True)
class InterestRow:
    invoice_no: str
    due_date: date
    outstanding: Decimal
    days_overdue: int
    chargeable_days: int
    interest: Decimal


def interest_for(outstanding: Decimal, days_overdue: int, rate_pct: Decimal, grace_days: int = 0) -> tuple[int, Decimal]:
    chargeable = max(0, days_overdue - grace_days)
    amount = outstanding * rate_pct / Decimal(100) * Decimal(chargeable) / Decimal(365)
    return chargeable, amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def interest_statement(invoices: list[Invoice], customer: str, as_of: date, rate_pct: Decimal, grace_days: int, disputed: set[tuple[str, str]],
                       waiver_days: int = 7, sender: str = "", company: str = "", language: str = "en") -> dict:
    key = customer_key(customer)
    mine = [i for i in invoices if customer_key(i.customer) == key]
    if not mine:
        raise ValueError(f"unknown customer {customer!r}")
    if not 1 <= waiver_days <= 30:
        raise ValueError("waiver_days must be from 1 to 30")
    if language not in ("en", "hinglish"):
        raise ValueError("language must be en or hinglish")
    display = mine[0].customer
    if rate_pct <= 0:
        return {"configured": False, "customer": display, "note": "Set the interest rate from your invoice terms in Settings first."}
    rows, skipped = [], []
    for i in sorted(mine, key=lambda i: (i.due_date or i.invoice_date, i.invoice_no)):
        a = assess(i, as_of)
        if a.status is not Status.OVERDUE:
            continue
        if (i.invoice_no, key) in disputed:
            skipped.append(i.invoice_no)
            continue
        chargeable, amount = interest_for(a.outstanding, a.days_overdue, rate_pct, grace_days)
        if amount > 0:
            rows.append(InterestRow(i.invoice_no, i.due_date, a.outstanding, a.days_overdue, chargeable, amount))
    overdue = sum((r.outstanding for r in rows), _ZERO)
    interest = sum((r.interest for r in rows), _ZERO)
    deadline = as_of + timedelta(days=waiver_days)
    return {
        "configured": True, "customer": display, "as_of": as_of.isoformat(), "rate": f"{rate_pct:.2f}", "grace_days": grace_days,
        "rows": [{"invoice_no": r.invoice_no, "due_date": r.due_date.isoformat(), "outstanding": f"{r.outstanding:.2f}", "days_overdue": r.days_overdue,
                  "chargeable_days": r.chargeable_days, "interest": f"{r.interest:.2f}"} for r in rows],
        "overdue": f"{overdue:.2f}", "interest": f"{interest:.2f}", "disputed_left_out": skipped,
        "deadline": deadline.isoformat(), "waiver_days": waiver_days, "terms_note": TERMS_NOTE, "msmed_note": MSMED_NOTE,
        "draft": None if not rows else waiver_draft(display, overdue, interest, rate_pct, as_of, deadline, sender, company, language == "hinglish"),
    }


def waiver_draft(customer: str, overdue: Decimal, interest: Decimal, rate_pct: Decimal, as_of: date, deadline: date, sender: str, company: str, hi: bool) -> dict:
    when, by = as_of.strftime("%d-%b-%Y"), deadline.strftime("%d-%b-%Y")
    rate = f"{rate_pct.normalize():f}"
    sign = "\n".join(x for x in ((f"Dhanyavaad,\n{sender}" if hi else f"Regards,\n{sender}") if sender else "", company) if x)
    if hi:
        wa = (f"Namaste {customer} team, hamare invoice terms ke hisaab se {rate}% p.a. par overdue raashi ka byaaj {when} tak lagbhag {format_inr(interest)} banta hai. "
              f"Agar poori overdue raashi {format_inr(overdue)} {by} tak mil jaati hai to hum yeh byaaj maaf kar denge. Kripya batayein kab tak bhej sakte hain.")
        subject = "Overdue payment: byaaj maafi ka prastaav"
    else:
        wa = (f"Dear {customer} team, as per our invoice terms, interest at {rate}% p.a. on the overdue amount comes to about {format_inr(interest)} as on {when}. "
              f"If the full overdue amount of {format_inr(overdue)} is received by {by}, we will waive this interest. Please let us know when you can pay.")
        subject = "Overdue payment: offer to waive interest"
    email = wa.replace(f" {customer} team, ", f" {customer} team,\n\n", 1) + (f"\n\n{sign}" if sign else "")
    return {"subject": subject, "whatsapp": wa + (f"\n{sign}" if sign else ""), "email": email}
