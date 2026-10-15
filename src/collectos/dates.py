"""Date parsing for Indian business exports. Day-first by convention."""

from datetime import date, datetime

_FORMATS = (
    "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y",
    "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d-%B-%Y",
    "%Y-%m-%d", "%Y/%m/%d",
    "%d-%m-%y", "%d/%m/%y",
)

MIN_YEAR, MAX_YEAR = 2000, 2100


class DateError(ValueError):
    """Raised when a value is not a recognisable, plausible date."""


def parse_date(value: object) -> date:
    """Parse a cell value to a date.

    Slash and dash dates are read day-first (15/10/2026 is 15 October). Anything
    outside 2000-2100 is rejected as a probable typo. Bare numbers (Excel serials)
    are rejected rather than guessed.
    """
    if isinstance(value, datetime):
        result = value.date()
    elif isinstance(value, date):
        result = value
    elif isinstance(value, str):
        result = _parse_text(value)
    else:
        raise DateError(f"not a date: {value!r}")

    if not MIN_YEAR <= result.year <= MAX_YEAR:
        raise DateError(f"implausible year in date: {value!r}")
    return result


def _parse_text(text: str) -> date:
    cleaned = text.strip()
    for fmt in _FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise DateError(f"unrecognised date format: {text!r}")
