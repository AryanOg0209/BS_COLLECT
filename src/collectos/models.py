"""Core domain records. Immutable; construction validates."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .money import to_money


class InvoiceError(ValueError):
    """Raised when an invoice record violates a basic invariant."""


def customer_key(name: str) -> str:
    """Case- and whitespace-insensitive identity for a customer name."""
    return " ".join(name.split()).casefold()


@dataclass(frozen=True)
class Invoice:
    invoice_no: str
    customer: str
    invoice_date: date
    due_date: date | None
    amount: Decimal
    paid: Decimal = Decimal("0.00")

    def __post_init__(self) -> None:
        if not self.invoice_no.strip():
            raise InvoiceError("invoice_no is required")
        if not self.customer.strip():
            raise InvoiceError(f"customer is required (invoice {self.invoice_no})")
        # Re-validating through to_money rejects floats and over-precise Decimals.
        amount, paid = to_money(self.amount), to_money(self.paid)
        if amount <= 0:
            raise InvoiceError(f"amount must be positive (invoice {self.invoice_no})")
        if paid < 0:
            raise InvoiceError(f"paid cannot be negative (invoice {self.invoice_no})")
        if self.due_date is not None and self.due_date < self.invoice_date:
            raise InvoiceError(f"due_date is before invoice_date (invoice {self.invoice_no})")
        object.__setattr__(self, "amount", amount)
        object.__setattr__(self, "paid", paid)

    @property
    def outstanding(self) -> Decimal:
        return self.amount - self.paid

    def with_credit_days(self, credit_days: int) -> "Invoice":
        """Return a copy with due_date = invoice_date + credit_days.

        Explicit opt-in: ageing never guesses a missing due date on its own.
        """
        if credit_days < 0:
            raise InvoiceError("credit_days cannot be negative")
        return Invoice(
            self.invoice_no,
            self.customer,
            self.invoice_date,
            self.invoice_date + timedelta(days=credit_days),
            self.amount,
            self.paid,
        )


@dataclass(frozen=True)
class Promise:
    """A customer's commitment to pay by a date, optionally for an amount."""

    id: int
    customer: str
    customer_key: str
    promised_date: date
    promised_amount: Decimal | None
    status: str
