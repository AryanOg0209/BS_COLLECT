"""Customer ROI calculator with explicit, editable assumptions.

Direct labour savings and possible financing benefits are kept separate on purpose.
Nothing here predicts that reminders collect faster; every benefit is a function of an
assumption the prospect sets, and the defaults are illustrative placeholders only.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

_PAISE = Decimal("0.01")
WEEKS_PER_YEAR = 52
MONTHS_PER_YEAR = 12
DAYS_PER_YEAR = 365


@dataclass(frozen=True)
class RoiInputs:
    open_invoices: int = 300                       # context only; not used in any formula
    overdue_invoices: int = 90                     # context only
    collection_hours_per_week: Decimal = Decimal("10")   # time spent chasing, noting and reporting
    reporting_hours_per_month: Decimal = Decimal("6")    # time spent building outstanding reports by hand
    hourly_cost: Decimal = Decimal("200")          # rupees per hour for the person doing the work
    reduction_pct: Decimal = Decimal("20")         # ASSUMED share of that time saved, percent
    pilot_fee: Decimal = Decimal("4000")           # one-off, covers the 4-week pilot
    monthly_fee: Decimal = Decimal("2500")         # ongoing service fee
    monthly_credit_sales: Decimal = Decimal("2500000")
    cost_of_funds_pct: Decimal = Decimal("12")     # annual, what the business pays to fund receivables
    days_faster: Decimal = Decimal("0")            # scenario: set only if the owner believes it


@dataclass(frozen=True)
class RoiResult:
    hours_per_month: Decimal
    hours_saved_per_month: Decimal
    labour_saving_per_month: Decimal
    net_labour_after_fee: Decimal
    pilot_payback_months: Decimal | None
    financing_saving_per_month: Decimal
    verdict: str


VERDICT_NEGATIVE = (
    "On labour time alone the monthly fee is more than the time saved at these assumptions. "
    "The case has to rest on other benefits the owner can name (visibility, discipline, fewer missed follow-ups)."
)
VERDICT_POSITIVE = "At these assumptions the time saved is worth more than the monthly fee. Check the assumptions with the owner."


def _q(value: Decimal) -> Decimal:
    return value.quantize(_PAISE, rounding=ROUND_HALF_UP)


def hours_per_month(i: RoiInputs) -> Decimal:
    return i.collection_hours_per_week * WEEKS_PER_YEAR / MONTHS_PER_YEAR + i.reporting_hours_per_month


def calculate(i: RoiInputs) -> RoiResult:
    hours = hours_per_month(i)
    saved = hours * i.reduction_pct / 100
    labour = saved * i.hourly_cost
    net = labour - i.monthly_fee
    payback = _q(i.pilot_fee / labour) if labour > 0 else None
    financing = i.monthly_credit_sales * i.days_faster / DAYS_PER_YEAR * i.cost_of_funds_pct / 100
    return RoiResult(_q(hours), _q(saved), _q(labour), _q(net), payback, _q(financing),
                     VERDICT_NEGATIVE if _q(net) < 0 else VERDICT_POSITIVE)


def labour_saving_at(i: RoiInputs, reduction_pct: Decimal) -> Decimal:
    return _q(hours_per_month(i) * reduction_pct / 100 * i.hourly_cost)


def financing_saving_at(i: RoiInputs, days: Decimal) -> Decimal:
    return _q(i.monthly_credit_sales * days / DAYS_PER_YEAR * i.cost_of_funds_pct / 100)


# ---- spreadsheet with live formulas ---------------------------------------------------

_INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")
_HEAD_FONT = Font(bold=True)
_INR = r"[>=10000000]##\,##\,##\,##0.00;[>=100000]##\,##\,##0.00;##,##0.00"

# (label, attribute, cell, number format, note)
INPUT_ROWS = [
    ("Open invoices (context only)", "open_invoices", "0", "From the prospect's outstanding list."),
    ("Overdue invoices (context only)", "overdue_invoices", "0", "From the prospect's outstanding list."),
    ("Hours per week spent chasing / noting / reporting", "collection_hours_per_week", "0.0", "Ask: 'How many hours last week?' Use their number."),
    ("Hours per month spent building outstanding reports by hand", "reporting_hours_per_month", "0.0", "Ask who builds it and how long it takes."),
    ("Cost per hour of that person (Rs)", "hourly_cost", "#,##0", "Monthly cost to the business / hours worked. Ask the owner."),
    ("ASSUMED share of that time saved (%)", "reduction_pct", "0", "An assumption, not a promise. Test 10-40 in the table below."),
    ("Pilot fee, one-off (Rs)", "pilot_fee", "#,##0", "Covers the 4-week pilot."),
    ("Monthly service fee after pilot (Rs)", "monthly_fee", "#,##0", ""),
    ("Credit sales per month (Rs)", "monthly_credit_sales", "#,##0", "Only needed for the financing scenario."),
    ("Business's annual cost of funds (%)", "cost_of_funds_pct", "0.0", "Loan / overdraft rate they actually pay."),
    ("SCENARIO: collections this many days faster", "days_faster", "0.0", "Leave 0 unless the owner argues a number. Not a promise."),
]
_FIRST_INPUT_ROW = 5


def _cell(attr: str) -> str:
    return f"$B${_FIRST_INPUT_ROW + [r[1] for r in INPUT_ROWS].index(attr)}"


def write_calculator(path: str | Path, defaults: RoiInputs = RoiInputs()) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Calculator"
    ws["A1"] = "Receivables service: what could it be worth to this business?"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = ("Yellow cells are inputs; replace the illustrative placeholders with the prospect's own numbers. "
                "Everything else is a formula. These are estimates from stated assumptions, not predictions or guarantees.")
    ws["A2"].alignment = Alignment(wrap_text=True)

    ws.cell(row=_FIRST_INPUT_ROW - 1, column=1, value="Inputs").font = _HEAD_FONT
    for n, (label, attr, fmt, note) in enumerate(INPUT_ROWS):
        row = _FIRST_INPUT_ROW + n
        ws.cell(row=row, column=1, value=label)
        cell = ws.cell(row=row, column=2, value=float(getattr(defaults, attr)) if not isinstance(getattr(defaults, attr), int) else getattr(defaults, attr))
        cell.fill, cell.number_format = _INPUT_FILL, fmt
        ws.cell(row=row, column=3, value=note)

    h, rep, cost, red = _cell("collection_hours_per_week"), _cell("reporting_hours_per_month"), _cell("hourly_cost"), _cell("reduction_pct")
    pilot, fee = _cell("pilot_fee"), _cell("monthly_fee")
    sales, rate, days = _cell("monthly_credit_sales"), _cell("cost_of_funds_pct"), _cell("days_faster")
    hours = f"({h}*{WEEKS_PER_YEAR}/{MONTHS_PER_YEAR}+{rep})"

    out = _FIRST_INPUT_ROW + len(INPUT_ROWS) + 2
    ws.cell(row=out - 1, column=1, value="Direct labour time (money the business would otherwise spend)").font = _HEAD_FONT
    rows = [
        ("Working hours per month on this", f"=ROUND({hours},2)", "0.00"),
        ("Hours saved per month (at the assumed share)", f"=ROUND({hours}*{red}/100,2)", "0.00"),
        ("Labour value of that time per month (Rs)", f"=ROUND({hours}*{red}/100*{cost},2)", _INR),
        ("Labour value minus monthly fee (Rs)", f"=ROUND({hours}*{red}/100*{cost}-{fee},2)", _INR),
        ("Months of labour savings to cover the pilot fee", f'=IF({hours}*{red}/100*{cost}>0,ROUND({pilot}/({hours}*{red}/100*{cost}),2),"n/a")', "0.00"),
    ]
    for n, (label, formula, fmt) in enumerate(rows):
        ws.cell(row=out + n, column=1, value=label)
        c = ws.cell(row=out + n, column=2, value=formula)
        c.number_format = fmt
    net_row = out + 3
    verdict_row = out + len(rows)
    ws.cell(row=verdict_row, column=1, value="Reading")
    v = ws.cell(row=verdict_row, column=2, value=f'=IF(B{net_row}<0,"{VERDICT_NEGATIVE}","{VERDICT_POSITIVE}")')
    v.alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=verdict_row, start_column=2, end_row=verdict_row, end_column=3)
    ws.row_dimensions[verdict_row].height = 48

    fin = verdict_row + 2
    ws.cell(row=fin, column=1, value="Possible financing benefit (separate; depends on collection timing, which no tool can promise)").font = _HEAD_FONT
    ws.cell(row=fin + 1, column=1, value="Interest saved per month if collections were that many days faster (Rs)")
    c = ws.cell(row=fin + 1, column=2, value=f"=ROUND({sales}*{days}/{DAYS_PER_YEAR}*{rate}/100,2)")
    c.number_format = _INR

    sens = fin + 4
    ws.cell(row=sens - 1, column=1, value="Sensitivity: labour value per month at different assumed time savings").font = _HEAD_FONT
    for c_idx, title in enumerate(("Assumed share of time saved (%)", "Labour value per month (Rs)", "Minus monthly fee (Rs)"), start=1):
        ws.cell(row=sens, column=c_idx, value=title).font = _HEAD_FONT
    for n, pct in enumerate((10, 20, 30, 40), start=1):
        r = sens + n
        ws.cell(row=r, column=1, value=pct)
        ws.cell(row=r, column=2, value=f"=ROUND({hours}*A{r}/100*{cost},2)").number_format = _INR
        ws.cell(row=r, column=3, value=f"=ROUND({hours}*A{r}/100*{cost}-{fee},2)").number_format = _INR

    scen = sens + 7
    ws.cell(row=scen - 1, column=1, value="Scenarios: interest saved per month if collections were N days faster (not a promise)").font = _HEAD_FONT
    for c_idx, title in enumerate(("Days faster", "Interest saved per month (Rs)"), start=1):
        ws.cell(row=scen, column=c_idx, value=title).font = _HEAD_FONT
    for n, d in enumerate((0, 3, 5, 10), start=1):
        r = scen + n
        ws.cell(row=r, column=1, value=d)
        ws.cell(row=r, column=2, value=f"=ROUND({sales}*A{r}/{DAYS_PER_YEAR}*{rate}/100,2)").number_format = _INR

    notes = scen + 7
    for n, line in enumerate((
        "How to use in a meeting: fill the yellow cells with the owner's own figures, then ask whether the result matches their intuition.",
        "Overdue balances are not recoverable money; this sheet does not treat them as savings.",
        "Hours saved depends on how much manual work exists today. If it is a few hours a month, the labour case will be thin.",
    ), start=0):
        ws.cell(row=notes + n, column=1, value=line)

    dv = DataValidation(type="decimal", operator="greaterThanOrEqual", formula1="0", allow_blank=False,
                        error="Enter a number of zero or more.")
    ws.add_data_validation(dv)
    dv.add(f"B{_FIRST_INPUT_ROW}:B{_FIRST_INPUT_ROW + len(INPUT_ROWS) - 1}")
    for col, width in zip("ABC", (62, 22, 70)):
        ws.column_dimensions[col].width = width
    wb.save(path)
    return Path(path)
