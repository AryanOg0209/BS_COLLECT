"""Morning brief: a short plain-text summary the owner can paste into WhatsApp.

Built from the same queue and promise data as the dashboard. Text only; nothing is sent.
"""

from datetime import date, timedelta
from decimal import Decimal

from .money import format_inr
from .overview import _queue_inputs
from .store import Store

_ZERO = Decimal("0.00")
TOP_CALLS = 3


def morning_brief(store: Store, as_of: date, company: str = "") -> dict:
    queue, _ = _queue_inputs(store, as_of)
    promises = store.open_promises()
    today_p = [p for p in promises if p.promised_date == as_of]
    broken = [p for p in promises if p.promised_date < as_of]
    since = (store._now() - timedelta(hours=24)).isoformat(timespec="seconds")
    came_in = store.payments_recorded_since(since)
    overdue_total = sum((q.overdue for q in queue.work), _ZERO) + sum((w.overdue for w in queue.waiting), _ZERO)

    lines = [f"{company + ' ' if company else ''}Morning brief, {as_of.strftime('%a %d %b %Y')}"]
    if queue.work or queue.waiting:
        lines.append(f"Overdue to follow up: {format_inr(overdue_total)} across {len(queue.work) + len(queue.waiting)} customers.")
    else:
        lines.append("Nothing overdue to follow up.")
    if queue.work:
        lines.append("Call today:")
        for n, q in enumerate(queue.work[:TOP_CALLS], start=1):
            lines.append(f"{n}. {q.customer}: {format_inr(q.overdue)}, {q.max_days_overdue} days ({q.stage_label.lower()})")
        if len(queue.work) > TOP_CALLS:
            lines.append(f"...and {len(queue.work) - TOP_CALLS} more in the work queue.")
    if today_p:
        names = ", ".join(f"{p.customer}{'' if p.promised_amount is None else ' ' + format_inr(p.promised_amount)}" for p in today_p[:4])
        lines.append(f"Promised for today: {names}{' ...' if len(today_p) > 4 else ''}.")
    if broken:
        lines.append(f"Broken promises to deal with: {len(broken)}.")
    lines.append(f"Payments recorded in the last 24 hours: {format_inr(came_in)}." if came_in > 0 else "No new payments recorded in the last 24 hours.")
    return {
        "as_of": as_of.isoformat(), "text": "\n".join(lines),
        "facts": {"to_call": len(queue.work), "promised_today": len(today_p), "broken": len(broken), "came_in_24h": f"{came_in:.2f}",
                  "overdue_followup": f"{overdue_total:.2f}"},
    }
