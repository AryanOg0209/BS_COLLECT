"""Command-line interface. Thin: parses arguments, calls the layers, prints results."""

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from . import roi, synthetic, web
from .ageing import Status, assess
from .dates import DateError, parse_date
from .importer import ImportFileError, import_invoices, import_rows
from .models import Invoice, customer_key
from .money import MoneyError, format_inr, to_money
from .readers import UnsupportedFile
from .reminders import Sender, draft_for_customer, draft_reminder
from .report import write_owner_report
from .store import CHANNELS, Store, StoreError
from .workqueue import Queue, build_queue

DEFAULT_DB = "data/collectos.db"
_USER_ERRORS = (StoreError, ImportFileError, UnsupportedFile, DateError, MoneyError, ValueError, FileNotFoundError)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.func(args)
    except _USER_ERRORS as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=os.environ.get("COLLECTOS_DB", DEFAULT_DB), help=f"database file (default {DEFAULT_DB})")
    common.add_argument("--as-of", help="date to evaluate against, DD-MM-YYYY (default: today)")
    common.add_argument("--actor", default=os.environ.get("USER", "unknown"), help="who is making this change")

    root = argparse.ArgumentParser(prog="collectos", description="Receivables follow-up toolkit (local-first).")
    sub = root.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import", parents=[common], help="import invoices from a CSV/XLSX export")
    p.add_argument("file")
    p.add_argument("--credit-days", type=int, help="fill MISSING due dates as invoice date + N days (recorded in the audit log)")
    p.add_argument("--allow-errors", action="store_true", help="save the good rows even if some rows were rejected")
    p.set_defaults(func=_cmd_import)

    p = sub.add_parser("queue", parents=[common], help="show who to chase today, and why")
    p.set_defaults(func=_cmd_queue)

    p = sub.add_parser("log", parents=[common], help="record a follow-up (and optional promise to pay)")
    p.add_argument("customer")
    p.add_argument("--channel", choices=CHANNELS, required=True)
    p.add_argument("--outcome", required=True, help="what happened, e.g. 'Promised part payment'")
    p.add_argument("--note", default="")
    p.add_argument("--promise-date", help="DD-MM-YYYY")
    p.add_argument("--promise-amount")
    p.set_defaults(func=_cmd_log)

    p = sub.add_parser("resolve", parents=[common], help="close a promise as kept, broken or cancelled")
    p.add_argument("promise_id", type=int)
    p.add_argument("status", choices=("kept", "broken", "cancelled"))
    p.add_argument("--note", default="")
    p.set_defaults(func=_cmd_resolve)

    p = sub.add_parser("draft", parents=[common], help="print a reminder draft for a customer (never sends)")
    p.add_argument("customer")
    p.add_argument("--sender", required=True, help="your name")
    p.add_argument("--company", required=True, help="your company name")
    p.set_defaults(func=_cmd_draft)

    p = sub.add_parser("report", parents=[common], help="write the Excel owner report")
    p.add_argument("out", help="output .xlsx path")
    p.add_argument("--company", required=True)
    p.set_defaults(func=_cmd_report)

    p = sub.add_parser("ui", help="open the dashboard in your browser (served from this computer only)")
    p.add_argument("--db", default=os.environ.get("COLLECTOS_DB", DEFAULT_DB))
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-open", action="store_true", help="do not open a browser window")
    p.add_argument("--read-only", action="store_true", help="disable all buttons that change data (useful when showing the screen)")
    p.set_defaults(func=_cmd_ui)

    p = sub.add_parser("roi-sheet", help="write the customer ROI calculator workbook (editable assumptions, live formulas)")
    p.add_argument("out", help="output .xlsx path")
    p.set_defaults(func=_cmd_roi_sheet)

    p = sub.add_parser("snapshot", parents=[common], help="record today's measures (take one on day 1 as the baseline, then weekly)")
    p.set_defaults(func=_cmd_snapshot)

    p = sub.add_parser("trend", parents=[common], help="show the recorded snapshots side by side")
    p.set_defaults(func=_cmd_trend)

    p = sub.add_parser("demo", help="build a complete demo kit from synthetic data in a new folder")
    p.add_argument("outdir")
    p.add_argument("--as-of", help="DD-MM-YYYY (default: today)")
    p.set_defaults(func=_cmd_demo)
    return root


# ---- commands -------------------------------------------------------------------


def _cmd_import(args) -> int:
    report = import_invoices(args.file, default_credit_days=args.credit_days)
    where = f" (sheet {report.sheet!r})" if report.sheet else ""
    print(f"{report.source}{where}: {len(report.invoices)} invoice(s) valid, {len(report.duplicates)} duplicate row(s), "
          f"{len(report.errors)} rejected, {len(report.skipped_rows)} blank/total row(s) skipped")
    for e in report.errors[:20]:
        print(f"  row {e.row_no}: {e.message}")
    if len(report.errors) > 20:
        print(f"  ... and {len(report.errors) - 20} more")
    for w in report.warnings:
        print(f"  warning: {w}")
    if report.errors and not args.allow_errors:
        print("Nothing saved. Fix the rows above, or re-run with --allow-errors to save the valid rows.")
        return 2
    with _open(args.db) as store:
        result = store.apply_import(report, actor=args.actor, allow_errors=args.allow_errors)
    print(f"Saved (import #{result.import_id}): {result.inserted} new, {result.updated} changed, {result.unchanged} unchanged.")
    for w in result.warnings:
        print(f"  warning: {w}")
    if result.missing_from_import:
        shown = ", ".join(result.missing_from_import[:10])
        more = len(result.missing_from_import) - 10
        print(f"  {len(result.missing_from_import)} known invoice(s) were not in this file (not closed automatically): "
              f"{shown}{f' ... +{more} more' if more > 0 else ''}")
    return 0


def _cmd_queue(args) -> int:
    with _open(args.db) as store:
        queue = _queue(store, _as_of(args))
    _print_queue(queue)
    return 0


def _cmd_log(args) -> int:
    promise_date = parse_date(args.promise_date) if args.promise_date else None
    amount = to_money(args.promise_amount) if args.promise_amount else None
    with _open(args.db) as store:
        fid, pid = store.log_followup(
            args.customer, args.channel, args.outcome, actor=args.actor, note=args.note,
            promise_date=promise_date, promise_amount=amount,
        )
    print(f"Logged follow-up #{fid}" + (f" and promise #{pid} for {promise_date:%d-%b-%Y}" if pid else ""))
    return 0


def _cmd_resolve(args) -> int:
    with _open(args.db) as store:
        store.resolve_promise(args.promise_id, args.status, actor=args.actor, note=args.note)
    print(f"Promise #{args.promise_id} marked {args.status}.")
    return 0


def _cmd_draft(args) -> int:
    with _open(args.db) as store:
        draft = draft_for_customer(store.invoices(), store.open_promises(), args.customer,
                                   Sender(args.sender, args.company), _as_of(args))
    print(f"[template: {draft.template}]\n\nSubject: {draft.subject}\n\n{draft.email_body}\n\n--- WhatsApp version ---\n{draft.whatsapp_text}")
    return 0


def _cmd_report(args) -> int:
    as_of = _as_of(args)
    with _open(args.db) as store:
        invoices = store.invoices()
        promises, followups, snapshots = store.open_promises(), store.followups(), store.snapshots()
        queue = build_queue(invoices, as_of, store.last_contacts(), promises)
    path = write_owner_report(args.out, company=args.company, as_of=as_of, invoices=invoices, queue=queue,
                              open_promises=promises, followups=followups, snapshots=snapshots)
    print(f"Wrote {path}")
    return 0


def _cmd_ui(args) -> int:
    web.serve(args.db, port=args.port, open_browser=not args.no_open, read_only=args.read_only)
    return 0


def _cmd_roi_sheet(args) -> int:
    print(f"Wrote {roi.write_calculator(args.out)}  (defaults are illustrative placeholders; replace them with the prospect's numbers)")
    return 0


def _cmd_snapshot(args) -> int:
    with _open(args.db) as store:
        had = bool(store.snapshots())
        snap = store.take_snapshot(_as_of(args), actor=args.actor)
    kind = "Snapshot" if had else "Baseline snapshot"
    print(f"{kind} for {snap.as_of:%d-%b-%Y} saved: outstanding {format_inr(snap.total_outstanding)}, "
          f"overdue {format_inr(snap.overdue)} across {snap.overdue_customers} customer(s), "
          f"payments seen in data since last snapshot {format_inr(snap.payments_observed)}.")
    return 0


def _cmd_trend(args) -> int:
    with _open(args.db) as store:
        snapshots = store.snapshots()
    if not snapshots:
        print("No snapshots yet. Run `collectos snapshot` after your first import to record the baseline.")
        return 0
    _print_trend(snapshots)
    return 0


def _cmd_demo(args) -> int:
    out = Path(args.outdir)
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} already exists and is not empty; choose a new folder")
    out.mkdir(parents=True, exist_ok=True)
    as_of = parse_date(args.as_of) if args.as_of else date.today()
    run_demo(out, as_of)
    return 0


def run_demo(out: Path, as_of: date) -> None:
    """Simulate five weeks as they would really have happened, oldest event first."""
    csv_path = synthetic.write_csv(out / "synthetic_invoices.csv", synthetic.generate_rows(as_of))
    db = out / "demo.db"

    def clock(days_ago: int):
        moment = datetime.combine(as_of - timedelta(days=days_ago), time(11, 0), tzinfo=timezone.utc)
        return lambda: moment

    final = import_invoices(csv_path)
    history = synthetic.generate_history(as_of)
    early = as_of - timedelta(days=max(history))
    # Follow-ups go to the biggest overdue customers that already exist in the earliest import.
    eligible = {customer_key(i.invoice.customer) for i in final.invoices if i.invoice.invoice_date <= early}
    ranked = [c for c in _ranked_by_overdue([i.invoice for i in final.invoices], as_of) if customer_key(c) in eligible]

    # (days ago, channel, outcome, promised date as days from as-of or None, note); index = customer rank.
    script = {
        0: (20, "call", "Said payment next week", -10, "broken promise example"),
        1: (1, "call", "Asked to call back", None, ""),
        2: (2, "call", "Will pay by Friday", 4, "pending promise example"),
        3: (18, "whatsapp", "Read, no reply", None, ""),
        4: (9, "call", "Promised part payment", -3, "kept promise example"),
    }
    # Each event is (days ago, order within the day, action taking a Store).
    events: list[tuple[int, int, Callable[[Store], object]]] = []

    def weekly_import(ago: int, rows: list[list[str]]) -> Callable[[Store], object]:
        def act(store: Store) -> None:
            store.apply_import(import_rows(list(enumerate(rows, start=1))), actor="demo")
            store.take_snapshot(as_of - timedelta(days=ago), actor="demo")
        return act

    def final_import(store: Store) -> None:
        store.apply_import(final, actor="demo", allow_errors=True)
        store.take_snapshot(as_of, actor="demo")

    promise_ids: dict[int, int | None] = {}

    def contact(rank: int, channel: str, outcome: str, promise_date: date | None, note: str) -> Callable[[Store], object]:
        def act(store: Store) -> None:
            promise_ids[rank] = store.log_followup(ranked[rank], channel, outcome, actor="demo", note=note,
                                                   promise_date=promise_date)[1]
        return act

    def mark_kept(rank: int) -> Callable[[Store], object]:
        def act(store: Store) -> None:
            if promise_ids.get(rank):
                store.resolve_promise(promise_ids[rank], "kept", actor="demo", note="Part payment received")
        return act

    for ago, rows in history.items():
        events.append((ago, 0, weekly_import(ago, rows)))
    events.append((0, 0, final_import))
    for rank, (ago, channel, outcome, delta, note) in script.items():
        if rank < len(ranked):
            events.append((ago, 1, contact(rank, channel, outcome, None if delta is None else as_of + timedelta(days=delta), note)))
    if len(ranked) > 4:
        events.append((1, 2, mark_kept(4)))

    for ago, _, action in sorted(events, key=lambda e: (-e[0], e[1])):
        with Store(db, now=clock(ago)) as store:
            action(store)

    print(f"Final export: {len(final.invoices)} invoices valid, {len(final.errors)} rows rejected, "
          f"{len(final.duplicates)} duplicate rows flagged, {len(final.warnings)} warning(s):")
    for e in final.errors:
        print(f"  row {e.row_no}: {e.message}")

    with Store(db, now=clock(0)) as store:
        queue = _queue(store, as_of)
        invoices, promises, followups, snapshots = store.invoices(), store.open_promises(), store.followups(), store.snapshots()
    xlsx = write_owner_report(out / "owner_report.xlsx", company="Demo Distributors (synthetic data)", as_of=as_of,
                              invoices=invoices, queue=queue, open_promises=promises, followups=followups,
                              snapshots=snapshots, warnings=final.warnings)
    drafts = out / "reminder_drafts.txt"
    blocks = []
    for item in queue.work[:3]:
        mine = [i for i in invoices if customer_key(i.customer) == customer_key(item.customer)]
        d = draft_reminder(item.customer, [assess(i, as_of) for i in mine], Sender("Your Name", "Demo Distributors"), as_of, item.broken_promises)
        blocks.append(f"=== {item.customer} [{d.template}] ===\nSubject: {d.subject}\n\n{d.email_body}\n\n--- WhatsApp ---\n{d.whatsapp_text}\n")
    drafts.write_text("\n".join(blocks), encoding="utf-8")

    print()
    _print_queue(queue)
    print("\nSynthetic five-week history:")
    _print_trend(snapshots)
    print(f"\nDemo kit written to {out}/\n  {csv_path.name}  (synthetic input)\n  {xlsx.name}  (owner report, incl. Trend sheet)\n  {drafts.name}  (reminder drafts, never sent)\n  {db.name}  (demo database)")


# ---- helpers --------------------------------------------------------------------


def _open(db: str) -> Store:
    path = Path(db)
    if str(db) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    return Store(path)


def _as_of(args) -> date:
    return parse_date(args.as_of) if args.as_of else date.today()


def _queue(store: Store, as_of: date) -> Queue:
    return build_queue(store.invoices(), as_of, store.last_contacts(), store.open_promises())


def _ranked_by_overdue(invoices: list[Invoice], as_of: date) -> list[str]:
    totals: dict[str, Decimal] = {}
    for i in invoices:
        a = assess(i, as_of)
        if a.status is Status.OVERDUE:
            totals[i.customer] = totals.get(i.customer, Decimal("0")) + a.outstanding
    return [c for c, _ in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0].casefold()))]


def _print_trend(snapshots) -> None:
    print(f"{'As of':<12}{'Outstanding':>16}{'Overdue':>16}{'>60d':>14}{'>90d':>14}{'Follow-ups':>11}{'Broken':>7}{'Paid seen':>14}")
    for x in snapshots:
        print(f"{x.as_of:%d-%b-%Y}  {format_inr(x.total_outstanding):>16}{format_inr(x.overdue):>16}"
              f"{format_inr(x.over60):>14}{format_inr(x.over90):>14}{x.followups_7d:>11}{x.broken_promises:>7}"
              f"{format_inr(x.payments_observed):>14}")


def _print_queue(queue: Queue) -> None:
    if not queue.work:
        print("Nobody to chase today.")
    for n, i in enumerate(queue.work, start=1):
        print(f"{n:>2}. {i.customer}  [{i.action}]  score {i.score}  overdue {format_inr(i.overdue)}  oldest {i.max_days_overdue}d")
        for reason in i.reasons:
            print(f"      - {reason}")
    if queue.waiting:
        print("\nWaiting (not chasing today):")
        for w in queue.waiting:
            print(f"  {w.customer}  {format_inr(w.overdue)}: {w.reason}")
    if queue.promises_to_review:
        print("\nPromises to review:")
        for w in queue.promises_to_review:
            print(f"  {w.customer}: {w.reason}")


if __name__ == "__main__":
    sys.exit(main())
