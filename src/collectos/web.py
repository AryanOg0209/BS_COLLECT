"""Local dashboard server.

Safety properties (each is covered by a test):
- binds to the loopback interface only, never to the network;
- rejects requests whose Host header is not this server (blocks DNS-rebinding);
- no CORS headers, a strict Content-Security-Policy, and nothing loaded from other origins;
- reads are plain GETs. The only writes are three small JSON POSTs (log a follow-up, resolve a
  promise, take a snapshot). Each needs a per-run secret token in a custom header (which a
  cross-site page cannot send without a CORS preflight we never grant), a same-origin Origin
  header, a JSON content type and a small body. `read_only=True` disables writes entirely;
- static files come from a fixed allow-list, so there is no path traversal.
All text shown in the page comes from imported files and is untrusted: the client renders
it with textContent only, never as HTML.
"""

import json
import secrets
import tempfile
import threading
import webbrowser
from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .ageing import CURRENT, DEFAULT_EDGES, NO_DUE_DATE, Status, assess, bucket_labels, summarise_by_customer
from .dates import DateError, parse_date
from .money import MoneyError, to_money
from .reminders import Sender, draft_for_customer
from .report import write_owner_report
from .store import Store, StoreError
from .workqueue import build_queue

WEB_DIR = Path(__file__).parent / "webui"
STATIC_FILES = {"index.html": "text/html; charset=utf-8", "app.css": "text/css; charset=utf-8",
                "app.js": "text/javascript; charset=utf-8", "logo.svg": "image/svg+xml"}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
_ZERO = Decimal("0.00")
WRITE_ROUTES = {"/api/followup", "/api/promise/resolve", "/api/snapshot"}
MAX_BODY = 64 * 1024
TOKEN_HEADER = "X-CollectOS-Token"


def _m(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


def build_overview(store: Store, as_of: date, db_name: str = "") -> dict:
    """Everything the dashboard needs in one JSON-serialisable dict. Money is sent as exact strings."""
    invoices = store.invoices()
    results = [assess(i, as_of) for i in invoices]
    open_ = [r for r in results if r.status not in (Status.PAID, Status.OVERPAID)]
    overdue = [r for r in open_ if r.status is Status.OVERDUE]
    promises = store.open_promises()
    queue = build_queue(invoices, as_of, store.last_contacts(), promises)
    snapshots = store.snapshots()

    total_outstanding = sum((r.outstanding for r in open_), _ZERO)
    total_overdue = sum((r.outstanding for r in overdue), _ZERO)
    buckets = [CURRENT, *bucket_labels(DEFAULT_EDGES), NO_DUE_DATE]
    ageing = [
        {"bucket": b,
         "amount": _m(sum((r.outstanding for r in open_ if r.bucket == b), _ZERO)),
         "count": sum(1 for r in open_ if r.bucket == b)}
        for b in buckets
    ]
    customers = sorted(summarise_by_customer(results).values(), key=lambda s: (-s.overdue, s.customer.casefold()))
    broken = [p for p in promises if p.promised_date < as_of]
    last_week = [f for f in store.followups() if (as_of - datetime.fromisoformat(f.ts).date()).days in range(0, 7)]

    return {
        "as_of": as_of.isoformat(),
        "db_name": db_name,
        "kpi": {
            "total_outstanding": _m(total_outstanding),
            "overdue": _m(total_overdue),
            "overdue_share": None if total_outstanding == 0 else f"{total_overdue / total_outstanding:.4f}",
            "open_invoices": len(open_),
            "overdue_invoices": len(overdue),
            "customers_overdue": len({r.invoice.customer for r in overdue}),
            "to_contact_today": len(queue.work),
            "waiting": len(queue.waiting),
            "broken_promises": len(broken),
            "followups_7d": len(last_week),
        },
        "ageing": ageing,
        "trend": [
            {"as_of": s.as_of.isoformat(), "total_outstanding": _m(s.total_outstanding), "overdue": _m(s.overdue),
             "over60": _m(s.over60), "over90": _m(s.over90), "followups_7d": s.followups_7d,
             "broken_promises": s.broken_promises, "payments_observed": _m(s.payments_observed)}
            for s in snapshots
        ],
        "queue": [
            {"customer": q.customer, "action": q.action, "score": q.score, "overdue": _m(q.overdue),
             "outstanding": _m(q.outstanding), "oldest_days": q.max_days_overdue,
             "days_since_contact": q.days_since_contact, "reasons": list(q.reasons),
             "broken_promise_dates": [p.promised_date.isoformat() for p in q.broken_promises]}
            for q in queue.work
        ],
        "waiting": [{"customer": w.customer, "overdue": _m(w.overdue), "reason": w.reason} for w in queue.waiting],
        "review": [{"customer": w.customer, "reason": w.reason} for w in queue.promises_to_review],
        "promises": [
            {"id": p.id, "customer": p.customer, "date": p.promised_date.isoformat(),
             "amount": _m(p.promised_amount), "state": "broken" if p.promised_date < as_of else "pending"}
            for p in sorted(promises, key=lambda p: (p.promised_date, p.customer.casefold()))
        ],
        "customers": [
            {"customer": s.customer, "outstanding": _m(s.outstanding), "overdue": _m(s.overdue),
             "open_invoices": s.open_invoices, "overdue_invoices": s.overdue_invoices,
             "oldest_days": s.max_days_overdue, "buckets": {k: _m(v) for k, v in s.by_bucket.items()}}
            for s in customers
        ],
        "followups": [
            {"customer": f.customer, "ts": f.ts, "channel": f.channel, "outcome": f.outcome, "actor": f.actor}
            for f in reversed(store.followups()[-15:])
        ],
    }


class _Handler(BaseHTTPRequestHandler):
    server_version = "CollectOS"
    sys_version = ""

    # -- plumbing --------------------------------------------------------------
    def log_message(self, fmt, *args):  # keep the terminal quiet; errors are reported explicitly
        pass

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    # -- routing ---------------------------------------------------------------
    def do_GET(self):  # noqa: N802
        if self.headers.get("Host", "") not in self.server.allowed_hosts:
            return self._error(403, "unexpected Host header")
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path in ("/", "/index.html"):
                return self._static("index.html")
            if url.path.startswith("/static/"):
                return self._static(url.path.removeprefix("/static/"))
            if url.path == "/api/session":
                return self._json(200, {"token": self.server.token, "writable": not self.server.read_only})
            if url.path == "/api/overview":
                return self._overview(query)
            if url.path == "/api/draft":
                return self._draft(query)
            if url.path == "/report.xlsx":
                return self._report(query)
        except DateError as exc:
            return self._error(400, str(exc))
        except ValueError as exc:
            return self._error(404 if "unknown customer" in str(exc) else 400, str(exc))
        return self._error(404, "not found")

    do_HEAD = do_GET  # noqa: N815

    def _method_not_allowed(self):
        self._send(405, b'{"error": "method not allowed"}', "application/json; charset=utf-8", {"Allow": "GET, HEAD"})

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _method_not_allowed  # noqa: N815

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        if path not in WRITE_ROUTES:
            return self._method_not_allowed()
        self.close_connection = True  # never leave an unread request body on a kept-alive connection
        if self.server.read_only:
            return self._error(405, "this dashboard is running in read-only mode")
        if self.headers.get("Host", "") not in self.server.allowed_hosts:
            return self._error(403, "unexpected Host header")
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {f"http://{h}" for h in self.server.allowed_hosts}:
            return self._error(403, "cross-origin request refused")
        supplied = self.headers.get(TOKEN_HEADER, "")
        if not secrets.compare_digest(supplied.encode(), self.server.token.encode()):
            return self._error(403, "missing or wrong session token; reload the page")
        if not (self.headers.get("Content-Type", "").split(";")[0].strip().lower() == "application/json"):
            return self._error(415, "send JSON")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._error(411, "Content-Length required")
        if not 0 <= length <= MAX_BODY:
            return self._error(413, f"body must be at most {MAX_BODY} bytes")
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
            handler = {"/api/followup": self._post_followup, "/api/promise/resolve": self._post_resolve,
                       "/api/snapshot": self._post_snapshot}[path]
            self._json(200, handler(body))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._error(400, "body is not valid JSON")
        except (StoreError, DateError, MoneyError, ValueError, KeyError, TypeError) as exc:
            self._error(400, f"missing field {exc}" if isinstance(exc, KeyError) else str(exc))

    # -- write handlers (each validates through the same Store rules as the CLI) ------
    def _post_followup(self, body: dict) -> dict:
        promise_date = parse_date(body["promise_date"]) if _text(body, "promise_date") else None
        amount = to_money(_text(body, "promise_amount")) if _text(body, "promise_amount") else None
        with Store(self.server.db_path) as store:
            followup_id, promise_id = store.log_followup(
                _text(body, "customer", required=True), _text(body, "channel", required=True),
                _text(body, "outcome", required=True), actor=_actor(body), note=_text(body, "note"),
                promise_date=promise_date, promise_amount=amount)
        return {"followup_id": followup_id, "promise_id": promise_id}

    def _post_resolve(self, body: dict) -> dict:
        promise_id = body["id"]
        if not isinstance(promise_id, int) or isinstance(promise_id, bool):
            raise ValueError("id must be a whole number")
        with Store(self.server.db_path) as store:
            store.resolve_promise(promise_id, _text(body, "status", required=True), actor=_actor(body), note=_text(body, "note"))
        return {"ok": True}

    def _post_snapshot(self, body: dict) -> dict:
        as_of = parse_date(_text(body, "as_of")) if _text(body, "as_of") else date.today()
        with Store(self.server.db_path) as store:
            snap = store.take_snapshot(as_of, actor=_actor(body))
        return {"id": snap.id, "as_of": snap.as_of.isoformat(), "overdue": _m(snap.overdue),
                "payments_observed": _m(snap.payments_observed)}

    # -- handlers --------------------------------------------------------------
    def _static(self, name: str) -> None:
        if name not in STATIC_FILES:  # an allow-list, so there is no path to traverse
            return self._error(404, "not found")
        path = WEB_DIR / name
        if not path.is_file():
            return self._error(404, "not found")
        self._send(200, path.read_bytes(), STATIC_FILES[name])

    def _as_of(self, query: dict) -> date:
        return parse_date(query["as_of"]) if query.get("as_of") else date.today()

    def _overview(self, query: dict) -> None:
        as_of = self._as_of(query)
        with Store(self.server.db_path) as store:
            self._json(200, build_overview(store, as_of, Path(self.server.db_path).name))

    def _draft(self, query: dict) -> None:
        customer = query.get("customer", "")
        if not customer:
            return self._error(400, "customer is required")
        sender = Sender(query.get("sender") or "Your name", query.get("company") or "Your company")
        with Store(self.server.db_path) as store:
            draft = draft_for_customer(store.invoices(), store.open_promises(), customer, sender, self._as_of(query))
        self._json(200, {"template": draft.template, "subject": draft.subject, "email": draft.email_body,
                         "whatsapp": draft.whatsapp_text})

    def _report(self, query: dict) -> None:
        as_of, company = self._as_of(query), (query.get("company") or "CollectOS")[:80]
        with Store(self.server.db_path) as store, tempfile.TemporaryDirectory() as tmp:
            invoices, promises = store.invoices(), store.open_promises()
            queue = build_queue(invoices, as_of, store.last_contacts(), promises)
            path = write_owner_report(Path(tmp) / "report.xlsx", company=company, as_of=as_of, invoices=invoices,
                                      queue=queue, open_promises=promises, followups=store.followups(),
                                      snapshots=store.snapshots())
            data = path.read_bytes()
        self._send(200, data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                   {"Content-Disposition": f'attachment; filename="owner-report-{as_of.isoformat()}.xlsx"'})


def _text(body: dict, key: str, required: bool = False, limit: int = 2000) -> str:
    value = body.get(key)
    if value is None or value == "":
        if required:
            raise ValueError(f"{key} is required")
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    value = value.strip()
    if len(value) > limit:  # refuse rather than silently cut a record short
        raise ValueError(f"{key} is too long (at most {limit} characters)")
    return value


def _actor(body: dict) -> str:
    return _text(body, "actor", limit=60) or "dashboard"


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, db_path: str | Path, port: int = 0, read_only: bool = False):
        super().__init__(("127.0.0.1", port), _Handler)  # loopback only
        self.db_path = str(db_path)
        self.read_only = read_only
        self.token = secrets.token_urlsafe(32)  # new on every start; never written to disk
        actual = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{actual}", f"localhost:{actual}"}

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/"


def make_server(db_path: str | Path, port: int = 0, read_only: bool = False) -> DashboardServer:
    if not Path(db_path).is_file():
        raise FileNotFoundError(f"database not found: {db_path} (import data first, or build one with `collectos demo`)")
    return DashboardServer(db_path, port, read_only)


def serve(db_path: str | Path, port: int = 8765, open_browser: bool = True, read_only: bool = False) -> None:
    server = make_server(db_path, port, read_only)
    mode = "read-only" if read_only else "can record follow-ups"
    print(f"CollectOS dashboard: {server.url}  ({mode}; this computer only; Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.4, webbrowser.open, args=(server.url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
