"""Reminder drafts. Plain text only; nothing is ever sent from here.

Drafts state facts taken from the data (invoice numbers, due dates, amounts) and
make no legal, interest or tax claims. A person reviews and sends them.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .ageing import Ageing, Status, assess
from .models import Invoice, Promise, customer_key
from .money import format_inr

MAX_LINES_EMAIL = 15
MAX_LINES_WHATSAPP = 5
FIRM_AFTER_DAYS = 60

FRIENDLY, FIRM, PROMISE = "friendly", "firm", "promise_followup"
_ZERO = Decimal("0.00")


@dataclass(frozen=True)
class Sender:
    person: str
    company: str


@dataclass(frozen=True)
class Draft:
    template: str
    subject: str
    email_body: str
    whatsapp_text: str


def draft_reminder(
    customer: str,
    assessments: Iterable[Ageing],
    sender: Sender,
    as_of: date,
    broken_promises: Iterable[Promise] = (),
) -> Draft:
    """Pick the template from the facts: broken promise > very overdue > ordinary."""
    overdue = sorted(
        (a for a in assessments if a.status is Status.OVERDUE),
        key=lambda a: (-a.days_overdue, a.invoice.invoice_no),
    )
    if not overdue:
        raise ValueError(f"{customer}: no overdue invoices to remind about")
    broken = sorted(broken_promises, key=lambda p: p.promised_date)

    total = sum((a.outstanding for a in overdue), _ZERO)
    oldest = overdue[0].days_overdue
    if broken:
        template = PROMISE
    elif oldest > FIRM_AFTER_DAYS:
        template = FIRM
    else:
        template = FRIENDLY

    signoff = f"Regards,\n{sender.person}\n{sender.company}"
    greeting = f"Dear {customer} team,"
    lines_email = _lines(overdue, MAX_LINES_EMAIL)
    lines_wa = _lines(overdue, MAX_LINES_WHATSAPP)
    total_text = format_inr(total)
    count = len(overdue)
    noun = "invoice" if count == 1 else "invoices"

    if template == PROMISE:
        p = broken[0]
        when = f"{p.promised_date:%d-%b-%Y}"
        amount = f" of {format_inr(p.promised_amount)}" if p.promised_amount is not None else ""
        intro = (
            f"Our records show that payment{amount} was expected from you by {when}, and we have "
            f"not yet received it. The following {noun} remain outstanding:"
        )
        ask = "Could you please confirm the payment status, or let us know the date by which we can expect it?"
        subject = f"Following up on payment expected by {when}"
        wa_intro = f"Hello, following up on the payment expected by {when}. Still outstanding ({total_text}):"
        wa_ask = "Could you confirm the status or a new date?"
    elif template == FIRM:
        intro = f"The following {noun} from {sender.company} {'is' if count == 1 else 'are'} well past the due date:"
        ask = "We request you to arrange payment at the earliest, or let us know the date by which you will pay."
        subject = f"Overdue payment: {count} {noun}, {total_text}"
        wa_intro = f"Hello, these {noun} are well past due ({total_text} total):"
        wa_ask = "Please arrange payment or share the date you will pay."
    else:
        intro = f"This is a gentle reminder about the following {noun}, now past the due date:"
        ask = "If any invoice has a query or issue, please tell us and we will look into it right away."
        subject = f"Payment reminder: {count} {noun}, {total_text}"
        wa_intro = f"Hello, a gentle reminder about {count} {noun} past the due date ({total_text} total):"
        wa_ask = "If already paid, please share the UTR/reference. Thank you."

    paid_note = "If you have already made this payment, please share the UTR/reference so we can update our records."
    email = "\n\n".join(
        [greeting, intro, lines_email, f"Total overdue: {total_text}", ask, paid_note, signoff]
    )
    whatsapp = "\n".join([wa_intro, lines_wa, wa_ask, f"- {sender.person}, {sender.company}"])
    return Draft(template, subject, email, whatsapp)


def _lines(overdue: list[Ageing], limit: int) -> str:
    shown = overdue[:limit]
    rows = [
        f"- {a.invoice.invoice_no}: due {a.invoice.due_date:%d-%b-%Y}, "
        f"{format_inr(a.outstanding)} ({a.days_overdue} days overdue)"
        for a in shown
    ]
    if len(overdue) > limit:
        rest = overdue[limit:]
        rest_total = sum((a.outstanding for a in rest), _ZERO)
        rows.append(f"- and {len(rest)} more invoice(s) totalling {format_inr(rest_total)}")
    return "\n".join(rows)


def draft_for_customer(
    invoices: Iterable[Invoice],
    open_promises: Iterable[Promise],
    customer: str,
    sender: Sender,
    as_of: date,
) -> Draft:
    """Build the draft for one customer from plain data (shared by the CLI and the web UI)."""
    key = customer_key(customer)
    mine = [i for i in invoices if customer_key(i.customer) == key]
    if not mine:
        raise ValueError(f"unknown customer {customer!r}")
    broken = [p for p in open_promises if p.customer_key == key and p.promised_date < as_of]
    return draft_reminder(mine[0].customer, [assess(i, as_of) for i in mine], sender, as_of, broken)
