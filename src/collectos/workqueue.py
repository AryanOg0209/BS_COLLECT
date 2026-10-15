"""Collections work queue: who to chase today, and exactly why.

Transparent additive rules, no model and no randomness. Every point in a score
appears as a human-readable reason, so a person can disagree with it and tune
QueueRules instead of trusting a black box.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .ageing import Status, assess, summarise_by_customer
from .models import Invoice, Promise, customer_key
from .money import format_inr


@dataclass(frozen=True)
class QueueRules:
    # (minimum overdue amount, points); the first matching tier wins.
    amount_tiers: tuple[tuple[Decimal, int], ...] = (
        (Decimal("100000"), 30),
        (Decimal("25000"), 20),
        (Decimal("5000"), 10),
        (Decimal("0.01"), 5),
    )
    # (minimum days overdue for the customer's oldest invoice, points)
    age_tiers: tuple[tuple[int, int], ...] = ((91, 30), (61, 20), (31, 10), (1, 5))
    broken_promise_points: int = 25
    never_contacted_points: int = 10
    stale_contact_days: int = 14
    stale_contact_points: int = 10
    cooldown_days: int = 3  # do not chase again this soon after a contact
    call_if_days_over: int = 30
    call_if_amount: Decimal = Decimal("50000")


@dataclass(frozen=True)
class QueueItem:
    customer: str
    score: int
    action: str
    reasons: tuple[str, ...]
    overdue: Decimal
    outstanding: Decimal
    max_days_overdue: int
    days_since_contact: int | None
    broken_promises: tuple[Promise, ...]


@dataclass(frozen=True)
class WaitingItem:
    customer: str
    overdue: Decimal
    reason: str


@dataclass
class Queue:
    work: list[QueueItem] = field(default_factory=list)
    waiting: list[WaitingItem] = field(default_factory=list)
    promises_to_review: list[str] = field(default_factory=list)


def build_queue(
    invoices: Iterable[Invoice],
    as_of: date,
    last_contact: Mapping[str, date],
    promises: Iterable[Promise],
    rules: QueueRules = QueueRules(),
) -> Queue:
    """Build today's queue.

    last_contact maps customer_key -> date of the latest follow-up.
    promises should be the *open* promises. A promise is pending through its
    promised date and counts as broken from the following day.
    """
    summaries = summarise_by_customer([assess(i, as_of) for i in invoices])
    open_promises: dict[str, list[Promise]] = {}
    for p in promises:
        open_promises.setdefault(p.customer_key, []).append(p)

    queue = Queue()
    for name, summary in summaries.items():
        key = customer_key(name)
        mine = open_promises.get(key, [])
        broken = tuple(p for p in mine if p.promised_date < as_of)
        pending = [p for p in mine if p.promised_date >= as_of]

        if summary.overdue <= 0:
            if mine:
                queue.promises_to_review.append(
                    f"{name}: has an open promise but nothing overdue; mark it kept or cancelled"
                )
            continue

        contact = last_contact.get(key)
        days_since = None if contact is None else (as_of - contact).days

        if not broken and pending:
            nxt = min(pending, key=lambda p: p.promised_date)
            queue.waiting.append(WaitingItem(name, summary.overdue, f"promised to pay by {nxt.promised_date:%d-%b-%Y}"))
            continue
        if not broken and days_since is not None and days_since < rules.cooldown_days:
            queue.waiting.append(WaitingItem(name, summary.overdue, f"contacted {days_since} day(s) ago"))
            continue

        score, reasons = _score(summary.overdue, summary.max_days_overdue, broken, days_since, rules)
        urgent = bool(broken) or summary.max_days_overdue > rules.call_if_days_over or summary.overdue >= rules.call_if_amount
        queue.work.append(
            QueueItem(
                customer=name,
                score=score,
                action="Call" if urgent else "Send reminder",
                reasons=tuple(reasons),
                overdue=summary.overdue,
                outstanding=summary.outstanding,
                max_days_overdue=summary.max_days_overdue,
                days_since_contact=days_since,
                broken_promises=broken,
            )
        )

    queue.work.sort(key=lambda i: (-i.score, -i.overdue, i.customer.casefold()))
    queue.waiting.sort(key=lambda w: (-w.overdue, w.customer.casefold()))
    queue.promises_to_review.sort()
    return queue


def _score(overdue: Decimal, max_days: int, broken: tuple[Promise, ...], days_since: int | None, rules: QueueRules):
    score, reasons = 0, []

    for floor, points in rules.amount_tiers:
        if overdue >= floor:
            score += points
            reasons.append(f"{format_inr(overdue)} overdue: +{points}")
            break

    for floor, points in rules.age_tiers:
        if max_days >= floor:
            score += points
            reasons.append(f"oldest invoice {max_days} days overdue: +{points}")
            break

    for p in sorted(broken, key=lambda p: p.promised_date):
        score += rules.broken_promise_points
        reasons.append(f"broken promise (was due {p.promised_date:%d-%b-%Y}): +{rules.broken_promise_points}")

    if days_since is None and rules.never_contacted_points:
        score += rules.never_contacted_points
        reasons.append(f"never contacted: +{rules.never_contacted_points}")
    elif days_since is not None and days_since >= rules.stale_contact_days and rules.stale_contact_points:
        score += rules.stale_contact_points
        reasons.append(f"last contact {days_since} days ago: +{rules.stale_contact_points}")

    return score, reasons
