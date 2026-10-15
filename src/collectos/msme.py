"""Informational flag for the MSME payment-time rule. Not tax or legal advice.

Section 43B(h) and the MSMED Act set payment-time limits (45 days where there is a written
agreement, 15 days otherwise) for buyers paying registered micro and small suppliers. Whether
it applies depends on the supplier's Udyam classification, the written terms and the date of
acceptance, which this tool cannot know. It therefore only marks invoices that are more than
45 days past their invoice date, so a person can ask their CA.
"""

from collections.abc import Iterable
from datetime import date

from .models import Invoice

MSME_DAYS = 45
CAUTION = "Informational only. Whether the MSME payment-time rule applies depends on your Udyam registration and written terms; confirm with your CA."


def past_msme_window(invoices: Iterable[Invoice], as_of: date) -> list[Invoice]:
    return [i for i in invoices if i.outstanding > 0 and (as_of - i.invoice_date).days > MSME_DAYS]
