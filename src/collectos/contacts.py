"""Contact details: validation and the links that let a person send a message themselves.

Nothing here sends anything. A WhatsApp or mailto link only opens the user's own app with the
text filled in; the person reads it and presses send.
"""

import re
from urllib.parse import quote

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalise_phone(raw: str, default_cc: str = "91") -> str:
    """Return +<country><number> or '' for blank. Raises ValueError for anything doubtful."""
    text = (raw or "").strip()
    if not text:
        return ""
    if re.search(r"[^\d+\-\s()./]", text):
        raise ValueError("phone may contain only digits, spaces and + - ( ) . /")
    digits = re.sub(r"\D", "", text)
    if text.startswith("+"):
        pass
    elif digits.startswith("00"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = default_cc + digits[1:]
    elif len(digits) == 10:
        digits = default_cc + digits
    if not 8 <= len(digits) <= 15:
        raise ValueError("phone number looks too short or too long (include the area code or country code)")
    return "+" + digits


def normalise_email(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if len(text) > 254 or not _EMAIL.match(text):
        raise ValueError("that does not look like an email address")
    return text


def whatsapp_link(phone: str, text: str) -> str | None:
    if not phone:
        return None
    return f"https://wa.me/{phone.lstrip('+')}?text={quote(text, safe='')}"


def mailto_link(email: str, subject: str, body: str) -> str | None:
    if not email:
        return None
    return f"mailto:{quote(email, safe='@')}?subject={quote(subject, safe='')}&body={quote(body, safe='')}"


def tel_link(phone: str) -> str | None:
    return f"tel:{phone}" if phone else None
