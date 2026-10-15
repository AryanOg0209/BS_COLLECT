"""Ageing rules: pure functions over Invoice, no I/O."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum

from .models import Invoice

DEFAULT_EDGES: tuple[int, ...] = (30, 60, 90)

CURRENT = "Current"
SETTLED = "Settled"
OVERPAID = "Overpaid"
NO_DUE_DATE = "No due date"

_ZERO = Decimal("0.00")


class Status(Enum):
    PAID = "paid"
    NOT_DUE = "not_due"
    DUE_TODAY = "due_today"
    OVERDUE = "overdue"
    NO_DUE_DATE = "no_due_date"
    OVERPAID = "overpaid"


@dataclass(frozen=True)
class Ageing:
    invoice: Invoice
    status: Status
    days_overdue: int
    bucket: str
    outstanding: Decimal
    is_partial: bool


def bucket_labels(edges: tuple[int, ...] = DEFAULT_EDGES) -> list[str]:
    """Ordered overdue bucket labels, e.g. ['1-30', '31-60', '61-90', '91+']."""
    _check_edges(edges)
    labels, start = [], 1
    for edge in edges:
        labels.append(f"{start}-{edge}")
        start = edge + 1
    labels.append(f"{start}+")
    return labels


def assess(invoice: Invoice, as_of: date, edges: tuple[int, ...] = DEFAULT_EDGES) -> Ageing:
    outstanding = invoice.outstanding
    if outstanding < 0:
        return Ageing(invoice, Status.OVERPAID, 0, OVERPAID, outstanding, False)
    if outstanding == 0:
        return Ageing(invoice, Status.PAID, 0, SETTLED, outstanding, False)

    partial = invoice.paid > 0
    if invoice.due_date is None:
        return Ageing(invoice, Status.NO_DUE_DATE, 0, NO_DUE_DATE, outstanding, partial)

    days = (as_of - invoice.due_date).days
    if days < 0:
        return Ageing(invoice, Status.NOT_DUE, 0, CURRENT, outstanding, partial)
    if days == 0:
        return Ageing(invoice, Status.DUE_TODAY, 0, CURRENT, outstanding, partial)
    return Ageing(invoice, Status.OVERDUE, days, _bucket_for(days, edges), outstanding, partial)


def _bucket_for(days_overdue: int, edges: tuple[int, ...]) -> str:
    labels = bucket_labels(edges)
    for edge, label in zip(edges, labels):
        if days_overdue <= edge:
            return label
    return labels[-1]


def _check_edges(edges: tuple[int, ...]) -> None:
    if not edges or edges[0] < 1 or any(b <= a for a, b in zip(edges, edges[1:])):
        raise ValueError(f"edges must be positive and strictly increasing: {edges}")


@dataclass
class CustomerSummary:
    customer: str
    outstanding: Decimal = _ZERO
    overdue: Decimal = _ZERO
    open_invoices: int = 0
    overdue_invoices: int = 0
    max_days_overdue: int = 0
    by_bucket: dict[str, Decimal] = field(default_factory=dict)


def summarise_by_customer(results: list[Ageing]) -> dict[str, CustomerSummary]:
    """Roll open balances up per customer. Settled and overpaid invoices are excluded."""
    summaries: dict[str, CustomerSummary] = defaultdict(lambda: CustomerSummary(""))
    for r in results:
        if r.status in (Status.PAID, Status.OVERPAID):
            continue
        name = r.invoice.customer
        s = summaries[name]
        s.customer = name
        s.outstanding += r.outstanding
        s.open_invoices += 1
        s.by_bucket[r.bucket] = s.by_bucket.get(r.bucket, _ZERO) + r.outstanding
        if r.status is Status.OVERDUE:
            s.overdue += r.outstanding
            s.overdue_invoices += 1
            s.max_days_overdue = max(s.max_days_overdue, r.days_overdue)
    return dict(summaries)
