"""Owner-level insights drawn from data already held: customers who have gone quiet, related firms seen together,
and how trustworthy the data itself is. Pure functions; nothing here changes anything.
"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from .ageing import Status, assess
from .models import Invoice, customer_key

_ZERO = Decimal("0.00")
BIG_BALANCE = Decimal("100000")


# ---- customers who have gone quiet --------------------------------------------------------------------------------------------

def dormant_customers(invoices: list[Invoice], as_of: date, min_orders: int = 3, window_days: int = 365, floor_days: int = 45, factor: int = 2) -> list[dict]:
    """Customers who used to buy regularly and have stopped.

    "Regularly" means at least `min_orders` distinct invoice dates in the last year. "Stopped" means the time since their
    last invoice is more than `factor` times their usual gap (and at least `floor_days`). Revenue at risk, found in data
    you already have; it has nothing to do with whether they owe you money.
    """
    start = as_of - timedelta(days=window_days)
    by_customer: dict[str, list[Invoice]] = defaultdict(list)
    for i in invoices:
        by_customer[customer_key(i.customer)].append(i)
    rows = []
    for mine in by_customer.values():
        recent = [i for i in mine if start <= i.invoice_date <= as_of]
        days = sorted({i.invoice_date for i in recent})
        if len(days) < min_orders:
            continue
        gap = (days[-1] - days[0]).days / (len(days) - 1)
        quiet = (as_of - days[-1]).days
        threshold = max(floor_days, factor * gap)
        if quiet <= threshold:
            continue
        span_months = max(1.0, (days[-1] - days[0]).days / 30 + 1)
        billed = sum((i.amount for i in recent), _ZERO)
        owed = sum((i.outstanding for i in mine if i.outstanding > 0), _ZERO)
        rows.append({"customer": mine[0].customer, "last_invoice": days[-1].isoformat(), "days_quiet": quiet, "usual_gap_days": round(gap),
                     "orders": len(days), "monthly_billing": f"{(billed / Decimal(str(round(span_months, 2)))).quantize(Decimal('0.01')):.2f}",
                     "open_balance": f"{owed:.2f}"})
    return sorted(rows, key=lambda r: -Decimal(r["monthly_billing"]))


# ---- related firms ----------------------------------------------------------------------------------------------------------

def group_summary(invoices: list[Invoice], profiles: dict, as_of: date) -> dict:
    """Customers the owner has put in a group (same promoter or family), seen together: combined balance, overdue and limit."""
    results = [assess(i, as_of) for i in invoices]
    per: dict[str, dict] = {}
    for r in results:
        if r.status in (Status.PAID, Status.OVERPAID):
            continue
        m = per.setdefault(customer_key(r.invoice.customer), {"customer": r.invoice.customer, "outstanding": _ZERO, "overdue": _ZERO, "oldest": 0})
        m["outstanding"] += r.outstanding
        if r.status is Status.OVERDUE:
            m["overdue"] += r.outstanding
            m["oldest"] = max(m["oldest"], r.days_overdue)
    names = {customer_key(i.customer): i.customer for i in invoices}
    groups: dict[str, list[str]] = defaultdict(list)
    for key, display in names.items():
        profile = profiles.get(key)
        if profile and profile.group:
            groups[profile.group].append(key)
    out = []
    for group, keys in groups.items():
        members, limits = [], []
        for key in sorted(keys, key=lambda k: names[k].casefold()):
            p = profiles[key]
            m = per.get(key, {"customer": names[key], "outstanding": _ZERO, "overdue": _ZERO, "oldest": 0})
            if p.credit_limit:
                limits.append(p.credit_limit)
            members.append({"customer": names[key], "outstanding": f"{m['outstanding']:.2f}", "overdue": f"{m['overdue']:.2f}", "oldest_days": m["oldest"],
                            "credit_limit": None if not p.credit_limit else f"{p.credit_limit:.2f}"})
        total = sum((Decimal(m["outstanding"]) for m in members), _ZERO)
        overdue = sum((Decimal(m["overdue"]) for m in members), _ZERO)
        limit = sum(limits, _ZERO) if limits else None
        out.append({"group": group, "members": members, "outstanding": f"{total:.2f}", "overdue": f"{overdue:.2f}",
                    "combined_limit": None if limit is None else f"{limit:.2f}", "over_limit": limit is not None and total > limit})
    out.sort(key=lambda g: -Decimal(g["overdue"]))
    return {"groups": out, "ungrouped": len(names) - sum(len(g["members"]) for g in out)}


def group_overdue_elsewhere(invoices: list[Invoice], profiles: dict, customer: str, as_of: date) -> tuple[str, Decimal, list[str]] | None:
    """(group name, overdue owed by the *other* firms in the group, their names), or None if the customer is not grouped or the others owe nothing late."""
    key = customer_key(customer)
    p = profiles.get(key)
    if not p or not p.group:
        return None
    other = defaultdict(lambda: _ZERO)
    for i in invoices:
        k = customer_key(i.customer)
        if k != key and profiles.get(k) and profiles[k].group == p.group:
            a = assess(i, as_of)
            if a.status is Status.OVERDUE:
                other[i.customer] += a.outstanding
    total = sum(other.values(), _ZERO)
    return (p.group, total, sorted(other)) if total > 0 else None


# ---- how healthy is the data ---------------------------------------------------------------------------------------------------

def health_report(invoices: list[Invoice], profiles: dict, duplicate_groups: list[list[str]], as_of: date, days_since_change: int | None) -> dict:
    """A 0-100 score for data quality, with each deduction explained. Weight x share affected; nothing is hidden."""
    results = [assess(i, as_of) for i in invoices]
    open_ = [r for r in results if r.status not in (Status.PAID, Status.OVERPAID)]
    overdue_customers = {customer_key(r.invoice.customer): r.invoice.customer for r in open_ if r.status is Status.OVERDUE}
    outstanding: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    for r in open_:
        outstanding[customer_key(r.invoice.customer)] += r.outstanding
    all_customers = {customer_key(i.customer) for i in invoices}

    no_due = [r for r in open_ if r.status is Status.NO_DUE_DATE]
    overpaid = [r for r in results if r.status is Status.OVERPAID]
    dup_names = sum(len(g) for g in duplicate_groups)
    no_contact = [k for k in overdue_customers if not (profiles.get(k) and (profiles[k].phone or profiles[k].email))]
    big = [k for k, v in outstanding.items() if v >= BIG_BALANCE]
    big_no_limit = [k for k in big if not (profiles.get(k) and profiles[k].credit_limit)]

    def share(n: int, of: int) -> float:
        return 0.0 if of == 0 else min(1.0, n / of)

    stale = 0.0 if days_since_change is None else min(1.0, max(0, days_since_change - 7) / 21)
    checks = [
        ("no_due_date", "Invoices without a due date", 25, share(len(no_due), len(open_)), len(no_due), "They cannot be aged, so they never reach the call list.", "import", "Fix in your export"),
        ("overpaid", "Invoices paid more than billed", 10, share(len(overpaid), len(results)), len(overpaid), "Usually an advance or a wrong entry.", "import", "Check your export"),
        ("duplicate_names", "Customer names that look like duplicates", 15, share(dup_names, len(all_customers)), dup_names, "The same customer under two spellings splits their balance.", "customers", "Review customers"),
        ("no_contact", "Overdue customers with no phone or email", 20, share(len(no_contact), len(overdue_customers)), len(no_contact), "You cannot open WhatsApp or mail for them.", "customers", "Add contact details"),
        ("stale", "Data not refreshed for over a week", 20, stale, 0 if days_since_change is None else max(0, days_since_change), "Old numbers make the call list wrong.", "import", "Import a fresh export"),
        ("no_limit", "Large balances with no credit limit", 10, share(len(big_no_limit), len(big)), len(big_no_limit), "Customers owing ₹1 lakh or more with no limit set.", "credit", "Set limits"),
    ]
    items, penalty = [], 0.0
    for key, label, weight, frac, count, why, view, fix in checks:
        p = weight * frac
        penalty += p
        items.append({"key": key, "label": label, "count": count, "penalty": round(p, 1), "weight": weight, "ok": p < 0.5, "why": why, "view": view, "fix": fix})
    return {"score": max(0, round(100 - penalty)), "items": items}
