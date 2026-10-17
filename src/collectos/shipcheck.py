"""'Can I ship this order?' A rule-of-thumb check before dispatch, from what the data already shows.

The point is to prevent the next overdue invoice rather than chase the last one, and to give sales and
accounts one shared set of rules instead of an argument. Every verdict comes with its reasons and the
rules used. It never blocks anything: the owner decides.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .ageing import Status, assess
from .models import Invoice, customer_key
from .money import format_inr

_ZERO = Decimal("0.00")
SHIP, CARE, ADVANCE, HOLD, NEW = "ship", "ship_with_care", "advance", "hold", "new_customer"
_SEVERITY = {SHIP: 0, CARE: 1, ADVANCE: 2, HOLD: 3}
HEADLINES = {
    SHIP: "Looks fine to ship",
    CARE: "Ship, but keep an eye on it",
    ADVANCE: "Ask for an advance first",
    HOLD: "Hold until the old dues are cleared",
    NEW: "No history with this customer",
}


@dataclass(frozen=True)
class ShipRules:
    hold_days: int = 60            # money overdue longer than this: hold
    repeat_broken: int = 2         # this many broken promises (and more broken than kept): advance


def ship_check(invoices: list[Invoice], customer: str, order_value: Decimal, as_of: date, credit_limit: Decimal | None = None,
               kept: int = 0, broken: int = 0, open_disputes: int = 0, do_not_contact: bool = False, rules: ShipRules = ShipRules(),
               group_elsewhere: tuple[str, Decimal, list[str]] | None = None) -> dict:
    if order_value <= 0:
        raise ValueError("order value must be more than zero")
    key = customer_key(customer)
    mine = [i for i in invoices if customer_key(i.customer) == key]
    rule_text = [f"Money overdue more than {rules.hold_days} days: hold until cleared.",
                 "Order value plus what they already owe above the credit limit you set: advance for the excess.",
                 f"{rules.repeat_broken} or more broken promises, more than kept: ask for an advance.",
                 "Anything overdue, a broken promise or an open dispute: ship with care."]
    if not mine:
        return {"customer": customer, "verdict": NEW, "headline": HEADLINES[NEW], "order_value": f"{order_value:.2f}", "advance": None, "figures": {},
                "reasons": [{"level": "warn", "text": "There are no invoices for this name in your data. Check the spelling, or treat them as a new customer: consider an advance or part-advance, and ask for references."}],
                "rules": rule_text}
    display = mine[0].customer
    results = [assess(i, as_of) for i in mine]
    open_ = [r for r in results if r.status not in (Status.PAID, Status.OVERPAID)]
    outstanding = sum((r.outstanding for r in open_), _ZERO)
    late = [r for r in open_ if r.status is Status.OVERDUE]
    overdue = sum((r.outstanding for r in late), _ZERO)
    oldest = max((r.days_overdue for r in late), default=0)
    after = outstanding + order_value
    level, reasons, advance = 0, [], None

    def add(verdict: str, kind: str, text: str) -> None:
        nonlocal level
        level = max(level, _SEVERITY[verdict])
        reasons.append({"level": kind, "text": text})

    if overdue > 0 and oldest > rules.hold_days:
        add(HOLD, "bad", f"{format_inr(overdue)} is overdue, the oldest {oldest} days (more than {rules.hold_days}). Clear the old dues before sending more goods.")
    elif overdue > 0:
        add(CARE, "warn", f"{format_inr(overdue)} is already overdue ({oldest} days at most).")
    if credit_limit is not None:
        if outstanding > credit_limit:
            add(HOLD, "bad", f"They already owe {format_inr(outstanding)}, above the credit limit of {format_inr(credit_limit)}.")
        elif after > credit_limit:
            advance = after - credit_limit
            add(ADVANCE, "warn", f"This order takes the total to {format_inr(after)}, {format_inr(advance)} above the credit limit of {format_inr(credit_limit)}. Ask for at least that as an advance.")
        else:
            reasons.append({"level": "ok", "text": f"Stays within the credit limit: {format_inr(after)} of {format_inr(credit_limit)}."})
    else:
        reasons.append({"level": "info", "text": "No credit limit is set for this customer, so the limit check was skipped."})
    if broken >= rules.repeat_broken and broken > kept:
        add(ADVANCE, "warn", f"Broke {broken} promises and kept {kept}. Ask for an advance (how much is your call).")
    elif broken > 0:
        add(CARE, "warn", f"Has broken {broken} promise{'s' if broken != 1 else ''} (kept {kept}).")
    elif kept > 0:
        reasons.append({"level": "ok", "text": f"Has kept {kept} promise{'s' if kept != 1 else ''} and broken none."})
    if open_disputes:
        add(CARE, "warn", f"{open_disputes} invoice{'s are' if open_disputes != 1 else ' is'} in dispute. Settle it or ship knowing it is still open.")
    if do_not_contact:
        add(CARE, "warn", "Marked do-not-contact. Check with whoever set that before shipping.")
    if group_elsewhere is not None:
        name, amount, others = group_elsewhere
        add(CARE, "warn", f"Other firms in the {name} group owe {format_inr(amount)} overdue ({', '.join(others[:3])}{' and more' if len(others) > 3 else ''}). Treat the group's exposure together.")
    if not late:
        reasons.insert(0, {"level": "ok", "text": "Nothing is overdue."})
    verdict = [v for v, s in _SEVERITY.items() if s == level][0]
    return {
        "customer": display, "verdict": verdict, "headline": HEADLINES[verdict], "order_value": f"{order_value:.2f}",
        "advance": None if advance is None else f"{advance:.2f}",
        "figures": {"outstanding": f"{outstanding:.2f}", "overdue": f"{overdue:.2f}", "oldest_days": oldest, "after_order": f"{after:.2f}",
                    "credit_limit": None if credit_limit is None else f"{credit_limit:.2f}"},
        "reasons": reasons, "rules": rule_text,
    }
