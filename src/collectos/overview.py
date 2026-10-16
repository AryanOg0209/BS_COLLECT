"""Builds the JSON the dashboard shows. Money is sent as exact strings, never floats.

Everything here is a plain function of the store, so it is tested without a server. Nothing
in it changes data.
"""

import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from .ageing import CURRENT, DEFAULT_EDGES, NO_DUE_DATE, Status, assess, bucket_labels, summarise_by_customer
from .matcher import STOPWORDS
from .models import customer_key
from .money import format_inr
from .msme import CAUTION, MSME_DAYS, past_msme_window
from .store import Store
from .store_extras import CustomerProfile
from .workqueue import STAGES, Queue, QueueItem, build_queue, stage_for, QueueRules

_ZERO = Decimal("0.00")


def _m(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


def _contact(p: CustomerProfile | None) -> dict:
    p = p or CustomerProfile("", "")
    return {"person": p.contact_person, "phone": p.phone, "email": p.email, "preferred": p.preferred_channel,
            "do_not_contact": p.do_not_contact, "credit_limit": _m(p.credit_limit), "notes": p.notes}


def _queue_inputs(store: Store, as_of: date):
    profiles = store.customer_profiles()
    queue = build_queue(
        store.invoices(), as_of, store.last_contacts(), store.open_promises(),
        disputed=store.disputed_invoice_keys(),
        do_not_contact={k for k, p in profiles.items() if p.do_not_contact},
        reliability=store.reliability(as_of),
        credit_limits={k: p.credit_limit for k, p in profiles.items() if p.credit_limit},
    )
    return queue, profiles


def _queue_item(q: QueueItem, profiles: dict) -> dict:
    return {
        "customer": q.customer, "action": q.action, "score": q.score, "overdue": _m(q.overdue), "outstanding": _m(q.outstanding),
        "oldest_days": q.max_days_overdue, "days_since_contact": q.days_since_contact, "reasons": list(q.reasons),
        "broken_promise_dates": [p.promised_date.isoformat() for p in q.broken_promises],
        "stage": q.stage, "stage_label": q.stage_label, "stage_why": q.stage_why,
        "disputed_overdue": _m(q.disputed_overdue), "credit_limit": _m(q.credit_limit), "total_outstanding": _m(q.total_outstanding),
        "over_limit": q.over_limit, "promises_kept": q.promises_kept, "promises_broken": q.promises_broken,
        "contact": _contact(profiles.get(customer_key(q.customer))),
    }


def _near_duplicates(names: list[str]) -> list[list[str]]:
    stop = {w.lower() for w in STOPWORDS}
    groups: dict[frozenset, list[str]] = {}
    for n in names:
        tokens = frozenset(t for t in re.findall(r"[a-z0-9]+", n.lower()) if len(t) >= 2 and t not in stop)
        if tokens:
            groups.setdefault(tokens, []).append(n)
    return [sorted(v) for v in groups.values() if len(v) > 1]


def build_overview(store: Store, as_of: date, db_name: str = "") -> dict:
    invoices = store.invoices()
    results = [assess(i, as_of) for i in invoices]
    open_ = [r for r in results if r.status not in (Status.PAID, Status.OVERPAID)]
    overdue = [r for r in open_ if r.status is Status.OVERDUE]
    promises = store.open_promises()
    queue, profiles = _queue_inputs(store, as_of)
    snapshots = store.snapshots()
    suggestions = {s.promise.id: s for s in store.promise_suggestions()}
    last = store.last_contacts()
    msme_on = store.get_setting("msme_supplier") == "1"

    total_outstanding = sum((r.outstanding for r in open_), _ZERO)
    total_overdue = sum((r.outstanding for r in overdue), _ZERO)
    buckets = [CURRENT, *bucket_labels(DEFAULT_EDGES), NO_DUE_DATE]
    ageing = [{"bucket": b, "amount": _m(sum((r.outstanding for r in open_ if r.bucket == b), _ZERO)), "count": sum(1 for r in open_ if r.bucket == b)}
              for b in buckets]
    customers = sorted(summarise_by_customer(results).values(), key=lambda s: (-s.overdue, s.customer.casefold()))
    broken = [p for p in promises if p.promised_date < as_of]
    last_week = [f for f in store.followups() if (as_of - datetime.fromisoformat(f.ts).date()).days in range(0, 7)]

    overdue_keys = {customer_key(r.invoice.customer) for r in overdue}
    contacted = {k for k in overdue_keys if k in last and 0 <= (as_of - last[k]).days <= 6}
    weight = sum((r.outstanding * r.days_overdue for r in overdue), _ZERO)
    avg_days = None if total_overdue == 0 else f"{(weight / total_overdue):.1f}"
    soon = [p for p in promises if as_of <= p.promised_date <= as_of + timedelta(days=7)]
    open_disputes = store.disputes("open")
    disputed_overdue = sum((d.outstanding for d in open_disputes
                            if any(r.invoice.invoice_no == d.invoice_no and customer_key(r.invoice.customer) == d.customer_key and r.status is Status.OVERDUE for r in overdue)), _ZERO)
    over_limit = [q for q in queue.work if q.over_limit]
    review = [q for q in queue.work if q.stage >= 4]
    no_due = [r for r in results if r.status is Status.NO_DUE_DATE]
    msme = past_msme_window(invoices, as_of) if msme_on else []
    missing_contact = [q for q in queue.work[:20] if not (profiles.get(customer_key(q.customer)) and (profiles[customer_key(q.customer)].phone or profiles[customer_key(q.customer)].email))]

    health = {
        "no_due_date": {"count": len(no_due), "amount": _m(sum((r.outstanding for r in no_due), _ZERO))},
        "overpaid": sum(1 for r in results if r.status is Status.OVERPAID),
        "near_duplicate_names": _near_duplicates(sorted({i.customer for i in invoices})),
        "top_queue_without_contact": len(missing_contact),
    }
    kpi = {
        "total_outstanding": _m(total_outstanding), "overdue": _m(total_overdue),
        "overdue_share": None if total_outstanding == 0 else f"{total_overdue / total_outstanding:.4f}",
        "open_invoices": len(open_), "overdue_invoices": len(overdue), "customers_overdue": len(overdue_keys),
        "to_contact_today": len(queue.work), "waiting": len(queue.waiting), "broken_promises": len(broken), "followups_7d": len(last_week),
        "contacted_7d": len(contacted), "coverage_7d": None if not overdue_keys else f"{len(contacted) / len(overdue_keys):.4f}",
        "avg_days_overdue": avg_days,
        "promised_7d_count": len(soon), "promised_7d_amount": _m(sum((p.promised_amount for p in soon if p.promised_amount), _ZERO)),
        "disputed_count": len(open_disputes), "disputed_overdue": _m(disputed_overdue),
        "over_limit": len(over_limit), "review_credit": len(review),
        "msme_past_window": len(msme), "msme_past_window_amount": _m(sum((i.outstanding for i in msme), _ZERO)) if msme_on else None,
        "likely_paid_promises": len(suggestions),
    }
    return {
        "as_of": as_of.isoformat(), "db_name": db_name, "kpi": kpi, "health": health, "ageing": ageing,
        "settings": {"msme_supplier": msme_on, "msme_caution": CAUTION, "msme_days": MSME_DAYS,
                     **{k: store.get_setting(k) for k in ("upi_id", "payee_name", "interest_rate", "interest_grace_days", "monthly_outgoings")}},
        "trend": [{"as_of": s.as_of.isoformat(), "total_outstanding": _m(s.total_outstanding), "overdue": _m(s.overdue), "over60": _m(s.over60),
                   "over90": _m(s.over90), "followups_7d": s.followups_7d, "broken_promises": s.broken_promises, "payments_observed": _m(s.payments_observed)}
                  for s in snapshots],
        "queue": [_queue_item(q, profiles) for q in queue.work],
        "waiting": [{"customer": w.customer, "overdue": _m(w.overdue), "reason": w.reason} for w in queue.waiting],
        "review": [{"customer": w.customer, "reason": w.reason} for w in queue.promises_to_review],
        "disputed_customers": [{"customer": w.customer, "overdue": _m(w.overdue), "reason": w.reason} for w in queue.disputed],
        "disputes": {
            "open": [_dispute(d) for d in open_disputes],
            "resolved": [_dispute(d) for d in store.disputes("resolved")[:20]],
        },
        "credit_watch": [{"customer": q.customer, "stage": q.stage, "stage_label": q.stage_label, "stage_why": q.stage_why,
                          "total_outstanding": _m(q.total_outstanding), "credit_limit": _m(q.credit_limit), "over_limit": q.over_limit, "overdue": _m(q.overdue)}
                         for q in sorted((q for q in queue.work if q.stage >= 3 or q.over_limit), key=lambda q: (-q.stage, -q.overdue))[:12]],
        "promises": [
            {"id": p.id, "customer": p.customer, "date": p.promised_date.isoformat(), "amount": _m(p.promised_amount),
             "state": "broken" if p.promised_date < as_of else "pending",
             "suggestion": None if p.id not in suggestions else {"kind": suggestions[p.id].kind, "observed": _m(suggestions[p.id].observed)}}
            for p in sorted(promises, key=lambda p: (p.promised_date, p.customer.casefold()))
        ],
        "customers": [
            {"customer": s.customer, "outstanding": _m(s.outstanding), "overdue": _m(s.overdue), "open_invoices": s.open_invoices,
             "overdue_invoices": s.overdue_invoices, "oldest_days": s.max_days_overdue, "buckets": {k: _m(v) for k, v in s.by_bucket.items()},
             "phone": profiles.get(customer_key(s.customer), CustomerProfile("", "")).phone,
             "do_not_contact": profiles.get(customer_key(s.customer), CustomerProfile("", "")).do_not_contact}
            for s in customers
        ],
        "followups": [{"customer": f.customer, "ts": f.ts, "channel": f.channel, "outcome": f.outcome, "actor": f.actor}
                      for f in reversed(store.followups()[-15:])],
        "briefing": _briefing(queue, kpi, health, open_disputes, over_limit, review, suggestions, soon, msme_on),
    }


def _dispute(d) -> dict:
    return {"id": d.id, "invoice_no": d.invoice_no, "customer": d.customer, "category": d.category, "note": d.note, "owner": d.owner,
            "status": d.status, "opened_ts": d.opened_ts, "opened_by": d.opened_by, "resolved_ts": d.resolved_ts,
            "resolution_note": d.resolution_note, "outstanding": _m(d.outstanding)}


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _briefing(queue: Queue, kpi: dict, health: dict, disputes, over_limit, review, suggestions, soon, msme_on) -> list[dict]:
    """The plain-language morning summary, most important first."""
    out: list[dict] = []

    def add(kind: str, level: str, text: str, view: str) -> None:
        out.append({"kind": kind, "level": level, "text": text, "view": view})

    if queue.work:
        total = sum((q.overdue for q in queue.work), _ZERO)
        add("call", "info", f"{_plural(len(queue.work), 'customer')} to contact today, {format_inr(total)} overdue. Start with {queue.work[0].customer}.", "queue")
    else:
        add("call", "ok", "Nobody needs chasing today.", "queue")
    if kpi["broken_promises"]:
        add("broken", "warn", f"{_plural(kpi['broken_promises'], 'promise')} broken: the promised date passed and it was not marked kept.", "promises")
    if suggestions:
        add("paid", "info", f"{_plural(len(suggestions), 'promise')} may already be paid (payments arrived since). Check and mark kept.", "promises")
    if soon:
        amt = kpi["promised_7d_amount"]
        suffix = f", {format_inr(Decimal(amt))} promised" if amt and Decimal(amt) > 0 else ""
        add("soon", "info", f"{_plural(len(soon), 'promise')} fall due in the next 7 days{suffix}.", "promises")
    if disputes:
        add("dispute", "info", f"{_plural(len(disputes), 'invoice')} in dispute. They are kept out of the chase list until resolved.", "disputes")
    if over_limit:
        add("limit", "warn", f"{_plural(len(over_limit), 'customer')} over the credit limit you set.", "credit")
    if review:
        add("review", "warn", f"{_plural(len(review), 'customer')} suggest reviewing credit terms (more than 90 days overdue or repeated broken promises). Your decision.", "credit")
    if health["no_due_date"]["count"]:
        add("health", "warn", f"{_plural(health['no_due_date']['count'], 'invoice')} have no due date and cannot be aged.", "health")
    if health["top_queue_without_contact"]:
        add("contact", "info", f"{_plural(health['top_queue_without_contact'], 'customer')} in today's list have no phone or email saved.", "queue")
    if kpi["coverage_7d"] is not None:
        add("coverage", "info", f"You contacted {kpi['contacted_7d']} of {kpi['customers_overdue']} overdue customers in the last 7 days.", "queue")
    if msme_on and kpi["msme_past_window"]:
        add("msme", "info", f"{_plural(kpi['msme_past_window'], 'invoice')} are more than 45 days from the invoice date. {CAUTION}", "customers")
    return out


# ---- customer 360 ---------------------------------------------------------------------------------------


def customer_detail(store: Store, name: str, as_of: date) -> dict:
    key = customer_key(name)
    invoices = [i for i in store.invoices() if customer_key(i.customer) == key]
    if not invoices:
        raise ValueError(f"unknown customer {name!r}")
    display = invoices[0].customer
    profile = store.customer_profile(display)
    results = [assess(i, as_of) for i in invoices]
    summary = next(iter(summarise_by_customer(results).values()), None)
    disputes = [d for d in store.disputes() if d.customer_key == key]
    open_disputes = {d.invoice_no: d for d in disputes if d.status == "open"}
    rel = store.reliability(as_of).get(key)
    kept, broken = (rel.kept, rel.broken) if rel else (0, 0)
    queue, profiles = _queue_inputs(store, as_of)
    item = next((q for q in queue.work if customer_key(q.customer) == key), None)
    waiting = next((w.reason for w in queue.waiting if customer_key(w.customer) == key), None)
    msme_on = store.get_setting("msme_supplier") == "1"

    outstanding = summary.outstanding if summary else _ZERO
    overdue_amt = summary.overdue if summary else _ZERO
    oldest = summary.max_days_overdue if summary else 0
    limit = profile.credit_limit
    if item is not None:
        stage, why = item.stage, item.stage_why
    elif overdue_amt > 0:
        stage, why = stage_for(overdue_amt, oldest, 0, broken, limit is not None and outstanding > limit, QueueRules())
    else:
        stage, why = 0, "nothing overdue"

    suggestions = [s for s in store.promise_suggestions() if s.promise.customer_key == key]
    return {
        "customer": display, "as_of": as_of.isoformat(), "profile": _contact(profile),
        "summary": {"outstanding": _m(outstanding), "overdue": _m(overdue_amt), "oldest_days": oldest,
                    "open_invoices": summary.open_invoices if summary else 0,
                    "credit_limit": _m(limit), "over_limit": limit is not None and outstanding > limit,
                    "utilisation": None if not limit else f"{outstanding / limit:.4f}"},
        "stage": stage, "stage_label": STAGES.get(stage, "Up to date"), "stage_why": why,
        "queue": None if item is None else _queue_item(item, profiles), "waiting_reason": waiting,
        "invoices": [
            {"invoice_no": r.invoice.invoice_no, "invoice_date": r.invoice.invoice_date.isoformat(),
             "due_date": None if r.invoice.due_date is None else r.invoice.due_date.isoformat(),
             "amount": _m(r.invoice.amount), "paid": _m(r.invoice.paid), "outstanding": _m(r.outstanding),
             "status": r.status.value, "days_overdue": r.days_overdue, "bucket": r.bucket, "partial": r.is_partial,
             "dispute": None if r.invoice.invoice_no not in open_disputes else
             {"id": open_disputes[r.invoice.invoice_no].id, "category": open_disputes[r.invoice.invoice_no].category},
             "past_msme_window": bool(msme_on and r.outstanding > 0 and (as_of - r.invoice.invoice_date).days > MSME_DAYS)}
            for r in sorted(results, key=lambda r: (r.status is Status.PAID, -r.days_overdue, r.invoice.invoice_no))
        ],
        "followups": [{"ts": f.ts, "channel": f.channel, "outcome": f.outcome, "note": f.note, "actor": f.actor}
                      for f in reversed(store.followups(display))][:50],
        "promises": [{"id": p.id, "date": p.promised_date.isoformat(), "amount": _m(p.promised_amount), "status": p.status,
                      "effective": "broken" if p.status == "open" and p.promised_date < as_of else p.status,
                      "created_ts": p.created_ts, "resolved_ts": p.resolved_ts, "note": p.resolution_note}
                     for p in reversed(store.promise_records(display))],
        "reliability": {"kept": kept, "broken": broken},
        "suggestions": [{"promise_id": s.promise.id, "kind": s.kind, "observed": _m(s.observed)} for s in suggestions],
        "disputes": [_dispute(d) for d in disputes],
        "msme": {"enabled": msme_on, "caution": CAUTION},
    }
