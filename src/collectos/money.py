"""Decimal-only money handling. Floats are rejected, never coerced."""

import re
from decimal import Decimal

PAISE = Decimal("0.01")

_CURRENCY_NOISE = re.compile(r"(?i)(₹|rs\.?|inr)")
_PLAIN_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


class MoneyError(ValueError):
    """Raised when a value cannot be represented as an exact amount in rupees."""


def to_money(value: object) -> Decimal:
    """Convert int / Decimal / str to an exact 2-decimal-place Decimal.

    Accepts Indian digit grouping ("1,23,456.50"), a leading currency marker
    (₹, Rs, INR) and accounting negatives ("(500.00)"). Rejects floats, bools,
    NaN/Infinity, exponent notation, and anything with more than two significant
    decimal places, because silently rounding a financial amount is not allowed.
    """
    if isinstance(value, bool) or isinstance(value, float):
        raise MoneyError(f"{type(value).__name__} is not allowed for money: {value!r}")

    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        result = _parse_text(value)
    else:
        raise MoneyError(f"unsupported type for money: {type(value).__name__}")

    if not result.is_finite():
        raise MoneyError(f"amount must be finite: {value!r}")

    quantized = result.quantize(PAISE)
    if quantized != result:
        raise MoneyError(f"amount has more than 2 decimal places: {value!r}")
    return quantized


def _parse_text(text: str) -> Decimal:
    cleaned = _CURRENCY_NOISE.sub("", text).replace(",", "").replace(" ", "").strip()
    negative = False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        negative = True
        cleaned = cleaned[1:-1]
    if not _PLAIN_NUMBER.match(cleaned):
        raise MoneyError(f"not a valid amount: {text!r}")
    amount = Decimal(cleaned)
    return -amount if negative else amount


def format_inr(amount: Decimal) -> str:
    """Format with Indian digit grouping, e.g. ₹2,50,000.00. Display only; never parse this back."""
    amount = to_money(amount)
    sign = "-" if amount < 0 else ""
    whole, frac = f"{abs(amount):.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return f"{sign}₹{','.join(groups + [tail])}.{frac}"
