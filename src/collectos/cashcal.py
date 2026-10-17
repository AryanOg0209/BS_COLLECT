"""Cash calendar: money that has a date attached, week by week, against a fixed monthly outgoing.

This is a planning view, not a forecast. It counts only
  * a post-dated cheque in hand (on its date; a cheque dated in the past counts today, as it can be deposited now),
  * a customer's own open promise (date and amount they gave), and
  * invoices not yet due, on their due date.
A cheque is never counted on top of the invoice it pays: each customer's counted money is capped at what they owe.
Each is "firm" or "shaky": shaky when the customer has broken more promises than kept, or is already
more than 30 days late on something else; a cheque is shaky only if that customer has a bounced cheque. Overdue money with no promise date is never counted; it is
listed separately so nobody mistakes it for incoming cash. Invoices in dispute are left out.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .ageing import Status, assess
from .models import Invoice, Promise, customer_key

_ZERO = Decimal("0.00")
SHAKY_LATE_DAYS = 30
WEEKS = 5


@dataclass(frozen=True)
class _Item:
    on: date
    customer: str
    amount: Decimal
    kind: str   # "promise" | "terms"
    firm: bool


def cash_calendar(invoices: list[Invoice], promises: list[Promise], reliability: dict, disputed: set[tuple[str, str]], as_of: date,
                  monthly_outgoings: Decimal = _ZERO, cheques: list = ()) -> dict:
    horizon = as_of + timedelta(days=WEEKS * 7)
    by_customer: dict[str, list] = defaultdict(list)
    for i in invoices:
        if (i.invoice_no, customer_key(i.customer)) in disputed:
            continue
        a = assess(i, as_of)
        if a.status not in (Status.PAID, Status.OVERPAID):
            by_customer[customer_key(i.customer)].append(a)

    items: list[_Item] = []
    no_amount = 0
    absorbed_overdue: dict[str, Decimal] = {}
    for key, assessments in by_customer.items():
        name = assessments[0].invoice.customer
        rel = reliability.get(key)
        broken = rel.broken if rel else 0
        kept = rel.kept if rel else 0
        oldest = max((a.days_overdue for a in assessments if a.status is Status.OVERDUE), default=0)
        firm = not (broken > kept or oldest > SHAKY_LATE_DAYS)
        room = sum((a.outstanding for a in assessments if a.status is Status.OVERDUE), _ZERO)   # a promise is about overdue money
        not_due = sorted(([a.invoice.due_date, a.outstanding] for a in assessments
                          if a.status in (Status.NOT_DUE, Status.DUE_TODAY) and a.invoice.due_date is not None), key=lambda e: e[0])
        mine = [c for c in cheques if c.customer_key == key]
        bounced_now = any(c.status == "bounced" for c in mine)
        for c in sorted((c for c in mine if c.status in ("received", "deposited")), key=lambda c: c.cheque_date):
            on = max(c.cheque_date, as_of)
            if on >= horizon:
                continue
            take = min(c.amount, room)
            room -= take
            absorbed_overdue[key] = absorbed_overdue.get(key, _ZERO) + take
            rest = c.amount - take
            for entry in not_due:           # whatever the cheque cannot cover of the overdue money pays the next invoices due
                t = min(rest, entry[1])
                entry[1] -= t
                rest -= t
            counted = c.amount - rest
            if counted > 0:
                items.append(_Item(on, name, counted, "cheque", not bounced_now))
        for p in sorted((p for p in promises if p.customer_key == key and as_of <= p.promised_date < horizon), key=lambda p: p.promised_date):
            if p.promised_amount is None:
                no_amount += 1
                continue
            amount = min(p.promised_amount, room)
            if amount > 0:
                room -= amount
                items.append(_Item(p.promised_date, name, amount, "promise", firm))
        for due, amount in not_due:
            if amount > 0 and as_of <= due < horizon:
                items.append(_Item(due, name, amount, "terms", firm))

    weeks = []
    for w in range(WEEKS):
        start = as_of + timedelta(days=7 * w)
        end = start + timedelta(days=6)
        chosen = sorted((x for x in items if start <= x.on <= end), key=lambda x: (x.on, -x.amount))
        firm = sum((x.amount for x in chosen if x.firm), _ZERO)
        shaky = sum((x.amount for x in chosen if not x.firm), _ZERO)
        weeks.append({"start": start.isoformat(), "end": end.isoformat(), "firm": f"{firm:.2f}", "shaky": f"{shaky:.2f}", "total": f"{firm + shaky:.2f}",
                      "items": [{"date": x.on.isoformat(), "customer": x.customer, "amount": f"{x.amount:.2f}", "kind": x.kind, "firm": x.firm} for x in chosen[:25]]})

    def within(days: int, firm_only: bool) -> Decimal:
        cutoff = as_of + timedelta(days=days)
        return sum((x.amount for x in items if x.on < cutoff and (x.firm or not firm_only)), _ZERO)

    undated = []
    for key, assessments in by_customer.items():
        late = [a for a in assessments if a.status is Status.OVERDUE]
        if not late:
            continue
        dated = any(p.customer_key == key and as_of <= p.promised_date < horizon and p.promised_amount for p in promises)
        left = sum((a.outstanding for a in late), _ZERO) - absorbed_overdue.get(key, _ZERO)
        if not dated and left > 0:
            undated.append((left, assessments[0].invoice.customer, max(a.days_overdue for a in late)))
    undated.sort(key=lambda t: -t[0])
    out_14 = (monthly_outgoings * Decimal(14) / Decimal(30)).quantize(Decimal("0.01"))
    return {
        "as_of": as_of.isoformat(), "weeks": weeks,
        "totals": {"firm_14": f"{within(14, True):.2f}", "all_14": f"{within(14, False):.2f}", "firm_30": f"{within(30, True):.2f}", "all_30": f"{within(30, False):.2f}"},
        "outgoings": {"monthly": f"{monthly_outgoings:.2f}", "days_14": f"{out_14:.2f}",
                      "gap_30_firm": f"{within(30, True) - monthly_outgoings:.2f}", "gap_30_all": f"{within(30, False) - monthly_outgoings:.2f}"},
        "undated": {"amount": f"{sum((t[0] for t in undated), _ZERO):.2f}", "customers": len(undated),
                    "top": [{"customer": c, "amount": f"{a:.2f}", "oldest_days": d} for a, c, d in undated[:5]]},
        "promises_without_amount": no_amount,
        "note": "Only money with a date counts: customers' own promises and invoices due by their terms. Shaky means the customer broke more promises than kept, or is over 30 days late on something. It is a planning view, not a prediction.",
    }
