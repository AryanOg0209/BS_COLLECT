"""Informational flag for the GST 180-day rule. Not tax advice.

Under Rule 37 of the CGST Rules, a buyer who has claimed input tax credit on an invoice may have to reverse it if the
supplier is not paid within 180 days of the invoice date (and may claim it again once paid). Whether this applies to a given
invoice depends on the buyer, the goods or service, and how the tax was charged, none of which this tool can know. It therefore
only marks open invoices that are near or past 180 days from the invoice date, so a person can ask their CA. It makes no claim
about any customer's tax position.
"""

from datetime import date

from .models import Invoice

ITC_DAYS = 180
ITC_WARN_DAYS = 150
CAUTION = ("Informational only. Under GST Rule 37, input tax credit on an invoice may become reversible by the buyer if the supplier is not paid within 180 days "
           "of the invoice date. Whether it applies depends on the buyer and the invoice; confirm with your CA before relying on it.")


def itc_state(invoice_date: date, as_of: date) -> str | None:
    """"past" beyond 180 days from the invoice date, "approaching" from 150 days, else None."""
    days = (as_of - invoice_date).days
    return "past" if days > ITC_DAYS else "approaching" if days >= ITC_WARN_DAYS else None


def itc_flags(invoices: list[Invoice], as_of: date) -> list[dict]:
    """Open invoices near or past the 180-day mark, oldest first."""
    rows = []
    for i in invoices:
        state = itc_state(i.invoice_date, as_of) if i.outstanding > 0 else None
        if state:
            rows.append({"invoice_no": i.invoice_no, "customer": i.customer, "days": (as_of - i.invoice_date).days, "state": state, "outstanding": f"{i.outstanding:.2f}"})
    return sorted(rows, key=lambda r: -r["days"])


def itc_note(flags: list[dict], hinglish: bool = False) -> str | None:
    """A customer-facing line to add to a reminder, listing the flagged invoices. None if there are none."""
    if not flags:
        return None
    listed = ", ".join(f"{f['invoice_no']} ({f['days']} days)" for f in flags[:5]) + (f" and {len(flags) - 5} more" if len(flags) > 5 else "")
    if hinglish:
        return (f"Dhyan dein: GST ke Rule 37 ke anusaar, agar invoice ki date se 180 din ke andar supplier ko payment nahi hota to liya hua input tax credit wapas karna pad sakta hai. "
                f"Ye invoice is seema ke paas hain: {listed}. Apne accountant se confirm kar lein.")
    return (f"A note: under GST Rule 37, input tax credit claimed on an invoice may need to be reversed if the supplier is not paid within 180 days of the invoice date. "
            f"These invoices are near that limit: {listed}. Please check with your accountant.")
