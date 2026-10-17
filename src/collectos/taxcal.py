"""Indian financial-year helpers (April to March). Pure functions."""

from datetime import date

_QUARTER_STARTS = ((4, 1), (7, 2), (10, 3), (1, 4))


def fy_quarter(on: date) -> tuple[str, int]:
    """("2026-27", 3) for any date from 1 Oct to 31 Dec 2026. Q1 = Apr-Jun, Q2 = Jul-Sep, Q3 = Oct-Dec, Q4 = Jan-Mar."""
    start_year = on.year if on.month >= 4 else on.year - 1
    quarter = 1 if 4 <= on.month <= 6 else 2 if 7 <= on.month <= 9 else 3 if 10 <= on.month <= 12 else 4
    return f"{start_year}-{(start_year + 1) % 100:02d}", quarter


def valid_fy(text: str) -> bool:
    """Accepts "2026-27": the second part must be the year after the first."""
    parts = text.split("-")
    return len(parts) == 2 and len(parts[0]) == 4 and parts[0].isdigit() and len(parts[1]) == 2 and parts[1].isdigit() \
        and (int(parts[0]) + 1) % 100 == int(parts[1])
