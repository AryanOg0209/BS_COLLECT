"""Collections work queue: who to chase today, and exactly why.

Transparent additive rules, no model and no randomness. Every point in a score
appears as a human-readable reason, so a person can disagree with it and tune
QueueRules instead of trusting a black box.
"""

from collections.abc import Collection, Iterable, Mapping
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
    # promise record: a customer who broke at least half of their (resolved) promises gets a visible bump
    reliability_min_promises: int = 2
    unreliable_points: int = 10
    over_limit_points: int = 10
    # escalation ladder: the stage is a suggestion for a person, never an automatic action
    owner_call_days: int = 60
    review_credit_days: int = 90
    owner_call_amount: Decimal = Decimal("200000")
    review_credit_broken: int = 3


STAGES = {1: "Friendly reminder", 2: "Firm follow-up", 3: "Owner call", 4: "Review credit terms"}


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
    stage: int = 1
    stage_label: str = STAGES[1]
    stage_why: str = ""
    disputed_overdue: Decimal = Decimal("0.00")
    credit_limit: Decimal | None = None
    total_outstanding: Decimal = Decimal("0.00")
    over_limit: bool = False
    promises_kept: int = 0
    promises_broken: int = 0


@dataclass(frozen=True)
class WaitingItem:
    customer: str
    overdue: Decimal
    reason: str


@dataclass
class Queue:
    work: list[QueueItem] = field(default_factory=list)
    waiting: list[WaitingItem] = field(default_factory=list)
    promises_to_review: list[WaitingItem] = field(default_factory=list)
    disputed: list[WaitingItem] = field(default_factory=list)


def build_queue(
    invoices: Iterable[Invoice],
    as_of: date,
    last_contact: Mapping[str, date],
    promises: Iterable[Promise],
    rules: QueueRules = QueueRules(),
    *,
    disputed: Collection[tuple[str, str]] = (),
    do_not_contact: Collection[str] = (),
    reliability: Mapping[str, object] = {},
    credit_limits: Mapping[str, Decimal] = {},
) -> Queue:
    """Build today's queue.

    last_contact maps customer_key -> date of the latest follow-up.
    promises should be the *open* promises. A promise is pending through its
    promised date and counts as broken from the following day.
    disputed holds (invoice_no, customer_key) of invoices under an open dispute: they are kept out of
    the chase list. do_not_contact holds customer keys. reliability maps customer_key to an object with
    .kept and .broken counts of past promises. credit_limits maps customer_key to a limit.
    """
    results = [assess(i, as_of) for i in invoices]
    in_dispute = [r for r in results if r.status is not Status.PAID and (r.invoice.invoice_no, customer_key(r.invoice.customer)) in disputed]
    chase = [r for r in results if r not in in_dispute]
    summaries = summarise_by_customer(chase)
    totals = summarise_by_customer(results)
    disputed_overdue: dict[str, Decimal] = {}
    for r in in_dispute:
        if r.status is Status.OVERDUE:
            k = customer_key(r.invoice.customer)
            disputed_overdue[k] = disputed_overdue.get(k, Decimal("0.00")) + r.outstanding

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
                    WaitingItem(name, summary.overdue, "has an open promise but nothing overdue; mark it kept or cancelled")
                )
            continue

        if key in do_not_contact:
            queue.waiting.append(WaitingItem(name, summary.overdue, "marked do-not-contact; handle this one outside the daily list"))
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

        rel = reliability.get(key)
        kept, rel_broken = (getattr(rel, "kept", 0), getattr(rel, "broken", 0)) if rel is not None else (0, 0)
        # reliability counts open promises that are past their date as broken; those are already scored
        # above as "broken promise", so only the resolved ones belong to the history here
        past_broken = max(0, rel_broken - len(broken))
        total_out = totals[name].outstanding if name in totals else summary.outstanding
        limit = credit_limits.get(key)
        over_limit = limit is not None and total_out > limit

        score, reasons = _score(summary.overdue, summary.max_days_overdue, broken, days_since, rules)
        if kept + past_broken >= rules.reliability_min_promises and past_broken * 2 >= kept + past_broken and rules.unreliable_points:
            score += rules.unreliable_points
            reasons.append(f"kept {kept} of {kept + past_broken} past promises: +{rules.unreliable_points}")
        if over_limit and rules.over_limit_points:
            score += rules.over_limit_points
            reasons.append(f"over credit limit ({format_inr(total_out)} of {format_inr(limit)}): +{rules.over_limit_points}")
        stage, why = stage_for(summary.overdue, summary.max_days_overdue, len(broken), past_broken, over_limit, rules)

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
                stage=stage,
                stage_label=STAGES[stage],
                stage_why=why,
                disputed_overdue=disputed_overdue.get(key, Decimal("0.00")),
                credit_limit=limit,
                total_outstanding=total_out,
                over_limit=over_limit,
                promises_kept=kept,
                promises_broken=past_broken,
            )
        )

    names = {customer_key(r.invoice.customer): r.invoice.customer for r in in_dispute}
    for key, amount in disputed_overdue.items():
        queue.disputed.append(WaitingItem(names[key], amount, "overdue invoice(s) in dispute: resolve the dispute before chasing"))

    queue.work.sort(key=lambda i: (-i.score, -i.overdue, i.customer.casefold()))
    queue.waiting.sort(key=lambda w: (-w.overdue, w.customer.casefold()))
    queue.promises_to_review.sort(key=lambda w: w.customer.casefold())
    queue.disputed.sort(key=lambda w: (-w.overdue, w.customer.casefold()))
    return queue


def stage_for(overdue: Decimal, max_days: int, broken_now: int, broken_before: int, over_limit: bool, rules: QueueRules) -> tuple[int, str]:
    """Suggested rung on the escalation ladder, with the first reason that put it there."""
    broken_total = broken_now + broken_before
    if max_days > rules.review_credit_days:
        return 4, f"oldest invoice is {max_days} days overdue"
    if broken_total >= rules.review_credit_broken:
        return 4, f"{broken_total} broken promises"
    if over_limit and max_days > rules.owner_call_days:
        return 4, "over credit limit and more than 60 days overdue"
    if max_days > rules.owner_call_days:
        return 3, f"oldest invoice is {max_days} days overdue"
    if broken_total >= 2:
        return 3, f"{broken_total} broken promises"
    if over_limit:
        return 3, "over credit limit"
    if overdue >= rules.owner_call_amount:
        return 3, f"{format_inr(overdue)} overdue"
    if max_days > 30:
        return 2, f"oldest invoice is {max_days} days overdue"
    if broken_total >= 1:
        return 2, "a promise was broken"
    return 1, "recently overdue"


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
