"""Deterministic synthetic receivables data for demos and tests.

All names are invented and carry a "(Demo)" suffix; any resemblance to a real
business is unintended. The data deliberately includes realistic mess so a demo
shows how problems are reported rather than hidden.
"""

import csv
import random
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from .money import format_inr

HEADER = ["Invoice No", "Customer", "Invoice Date", "Due Date", "Amount", "Paid"]

_NAMES = [
    "Aarav Fasteners", "Bharat Pipes", "Chandra Polymers", "Deepak Auto Spares", "Eastern Cables",
    "Falcon Packaging", "Gokul Hardware", "Himalaya Textiles", "Indus Lubricants", "Jyoti Electricals",
    "Kaveri Agro Tools", "Lakshmi Steel", "Malwa Chemicals", "Navkar Plastics", "Omkar Bearings",
    "Prime Tiles", "Quality Castings", "Rajdhani Paper", "Sagar Engineering", "Tara Foods Supply",
    "Uday Tools", "Vijay Valves", "Western Pumps", "Yash Industrial",
]


def generate_rows(as_of: date, seed: int = 2026, customers: int = 24) -> list[list[str]]:
    rng = random.Random(seed)
    rows: list[list[str]] = []
    counter = 1000
    for name in _NAMES[:customers]:
        customer = f"{name} (Demo)"
        credit = rng.choice([15, 30, 45])
        for _ in range(rng.randint(2, 7)):
            counter += 1
            invoice_date = as_of - timedelta(days=min(220, 1 + int(rng.expovariate(1 / 55))))  # mostly recent, a long tail of old ones
            due = invoice_date + timedelta(days=credit)
            amount = (Decimal(rng.choice([8000, 15000, 40000, 85000, 160000, 320000])) * Decimal(str(round(rng.uniform(0.6, 1.4), 2)))).quantize(Decimal("0.01"))
            roll = rng.random()
            if roll < 0.45:
                paid = Decimal("0.00")
            elif roll < 0.65:
                paid = (amount * Decimal(str(round(rng.uniform(0.3, 0.7), 2)))).quantize(Decimal("1"), rounding="ROUND_DOWN").quantize(Decimal("0.01"))
            else:
                paid = amount
            rows.append([f"INV-{counter:04d}", customer, _d(invoice_date), _d(due), _amt(amount, rng), _amt(paid, rng)])

    # Realistic mess, at fixed positions so output stays deterministic.
    for idx in (3, 17, 31):
        rows[idx][3] = ""                       # missing due date
    rows.insert(6, list(rows[5]))               # exact duplicate row
    rows.insert(41, list(rows[40]))             # exact duplicate row
    bad_amount = list(rows[9]); bad_amount[0] = "INV-9001"; bad_amount[4] = "N/A"
    bad_date = list(rows[12]); bad_date[0] = "INV-9002"; bad_date[2] = "31-02-2026"
    rows += [bad_amount, bad_date, ["", "Grand Total", "", "", "", ""]]
    return [HEADER, *rows]


def write_csv(path: str | Path, rows: list[list[str]]) -> Path:
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)
    return path


def _d(value: date) -> str:
    return value.strftime("%d-%m-%Y")


def _amt(value: Decimal, rng: random.Random) -> str:
    # Mix plain and Indian-formatted amounts, as real exports do.
    return format_inr(value) if rng.random() < 0.25 else f"{value:.2f}"
