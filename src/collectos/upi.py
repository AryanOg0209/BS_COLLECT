"""UPI payment details for reminders: a plain `upi://pay` link, a QR code and a text line.

No payment gateway and nothing is sent or collected by CollectOS: the customer pays with their own UPI
app, straight to the owner's own UPI id, and the owner sees it in their bank as usual. The QR code is
drawn locally (the `segno` library, no network).
"""

import io
import re
from decimal import Decimal
from urllib.parse import quote

from .money import format_inr, to_money

_VPA = re.compile(r"^[A-Za-z0-9._-]{2,64}@[A-Za-z][A-Za-z0-9]{1,31}$")
_PAYEE_JUNK = re.compile(r"[^A-Za-z0-9 .&'()/-]")
_NOTE_JUNK = re.compile(r"[^A-Za-z0-9 ._/#-]")
MAX_PAYEE, MAX_NOTE = 40, 50


def valid_vpa(value: str) -> bool:
    return bool(_VPA.match(value or ""))


def clean_payee(value: str) -> str:
    return " ".join(_PAYEE_JUNK.sub("", value or "").split())[:MAX_PAYEE]


def clean_note(value: str) -> str:
    return " ".join(_NOTE_JUNK.sub("", value or "").split())[:MAX_NOTE]


def upi_link(vpa: str, payee: str, amount: Decimal | None = None, note: str = "") -> str:
    """Build a upi://pay link. Raises ValueError for an invalid id, so a bad setting never makes a bad QR."""
    if not valid_vpa(vpa):
        raise ValueError("the UPI id in Settings is not valid")
    parts = [("pa", vpa), ("pn", clean_payee(payee) or vpa), ("cu", "INR")]
    if amount is not None:
        amount = to_money(amount)
        if amount <= 0:
            raise ValueError("amount must be more than zero")
        parts.insert(2, ("am", f"{amount:.2f}"))
    if clean_note(note):
        parts.append(("tn", clean_note(note)))
    return "upi://pay?" + "&".join(f"{k}={quote(v, safe='@')}" for k, v in parts)


def qr_svg(data: str) -> bytes | None:
    """The QR code as SVG bytes, or None if the optional library is missing."""
    try:
        import segno
    except ImportError:
        return None
    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="svg", scale=6, border=2, dark="#000000", light="#ffffff", xmldecl=False)
    return buf.getvalue()


def payment_line(vpa: str, amount: Decimal | None, note: str, hinglish: bool = False) -> str:
    """Plain text for WhatsApp/email. (A upi:// link is not tappable in most chat apps, so show the id.)"""
    amt = f" {format_inr(amount)}" if amount is not None and amount > 0 else ""
    ref = f" (note: {clean_note(note)})" if clean_note(note) else ""
    if hinglish:
        return f"UPI se bhej sakte hain: {vpa}{amt}{ref}"
    return f"You can pay by UPI to {vpa}{amt}{ref}."
