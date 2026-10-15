"""Local dashboard server.

Safety properties (each is covered by a test):
- binds to the loopback interface only, never to the network;
- rejects requests whose Host header is not this server (blocks DNS-rebinding);
- no CORS headers, a strict Content-Security-Policy, and nothing loaded from other origins;
- reads are plain GETs. Writes are a fixed list of POSTs (see WRITE_ROUTES): JSON for small
  actions, and raw-file uploads for imports and bank statements. Each needs a per-run secret
  token in a custom header (which a cross-site page cannot send without a CORS preflight we
  never grant), a same-origin Origin header, the exact content type for its kind and a size
  cap. Uploaded files live in a private temp folder under random names and are deleted after
  use. `read_only=True` disables writes entirely;
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
from urllib.parse import parse_qs, unquote, urlparse

from .contacts import mailto_link, tel_link, whatsapp_link
from .dates import DateError, parse_date
from .importer import import_invoices
from .matcher import StatementError, parse_statement, suggest
from .money import MoneyError, to_money
from .readers import read_sheets
from .reminders import LANGUAGES, Sender, draft_for_customer
from .report import write_owner_report
from .store import Store, StoreError
from .uploads import MAX_UPLOAD, UploadError, UploadStore
from .overview import _m, build_overview, customer_detail
from .workqueue import build_queue

WEB_DIR = Path(__file__).parent / "webui"
STATIC_FILES = {"index.html": "text/html; charset=utf-8", "app.css": "text/css; charset=utf-8",
                "app.js": "text/javascript; charset=utf-8", "features.js": "text/javascript; charset=utf-8", "logo.svg": "image/svg+xml"}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
_ZERO = Decimal("0.00")
# path -> (kind, handler name). kind "json" bodies are small objects; kind "upload" bodies are raw files.
WRITE_ROUTES = {
    "/api/followup": ("json", "_post_followup"),
    "/api/promise/resolve": ("json", "_post_resolve"),
    "/api/snapshot": ("json", "_post_snapshot"),
    "/api/customer/update": ("json", "_post_customer"),
    "/api/dispute/open": ("json", "_post_dispute_open"),
    "/api/dispute/resolve": ("json", "_post_dispute_resolve"),
    "/api/setting": ("json", "_post_setting"),
    "/api/invoice/settle": ("json", "_post_settle"),
    "/api/import/apply": ("json", "_post_import_apply"),
    "/api/import/preview": ("upload", "_post_import_preview"),
    "/api/statement/match": ("upload", "_post_statement"),
}
CONTENT_TYPES = {"json": "application/json", "upload": "application/octet-stream"}
MAX_BODY = 64 * 1024
TOKEN_HEADER = "X-CollectOS-Token"


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
            if url.path == "/api/customer":
                return self._customer(query)
            if url.path == "/api/activity":
                return self._activity(query)
            if url.path == "/backup.db":
                return self._backup()
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
        url = urlparse(self.path)
        route = WRITE_ROUTES.get(url.path)
        if route is None:
            return self._method_not_allowed()
        kind, handler_name = route
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
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != CONTENT_TYPES[kind]:
            return self._error(415, "send JSON" if kind == "json" else "send the raw file as application/octet-stream")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._error(411, "Content-Length required")
        limit = MAX_BODY if kind == "json" else MAX_UPLOAD
        if not 0 <= length <= limit:
            return self._error(413, f"body must be at most {limit} bytes")
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            data = self.rfile.read(length)
            handler = getattr(self, handler_name)
            if kind == "json":
                body = json.loads(data or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("body must be a JSON object")
                result = handler(body)
            else:
                result = handler(data, query)
            self._json(200, result)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._error(400, "body is not valid JSON")
        except (StoreError, DateError, MoneyError, ValueError, KeyError, TypeError) as exc:
            self._error(400, f"missing field {exc}" if isinstance(exc, KeyError) else str(exc))
        except Exception:  # an unreadable or hostile file must never produce a stack trace for the page
            if kind == "upload":
                self._error(400, "could not read that file; check that it is a real .csv or .xlsx export")
            else:
                raise

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

    def _post_customer(self, body: dict) -> dict:
        name = _text(body, "customer", required=True)
        changes: dict = {}
        for key in ("contact_person", "phone", "email", "preferred_channel", "notes"):
            if key in body:
                changes[key] = _text(body, key, limit=2000)
        if "credit_limit" in body:
            changes["credit_limit"] = _text(body, "credit_limit") or None
        if "do_not_contact" in body:
            changes["do_not_contact"] = body["do_not_contact"]
        with Store(self.server.db_path) as store:
            p = store.update_customer(name, _actor(body), **changes)
        return {"customer": p.name, "phone": p.phone, "email": p.email, "do_not_contact": p.do_not_contact,
                "credit_limit": None if p.credit_limit is None else f"{p.credit_limit:.2f}"}

    def _post_dispute_open(self, body: dict) -> dict:
        with Store(self.server.db_path) as store:
            dispute_id = store.open_dispute(_text(body, "invoice_no", required=True), _text(body, "customer", required=True),
                                            _text(body, "category", required=True), _actor(body),
                                            note=_text(body, "note"), owner=_text(body, "owner", limit=120))
        return {"id": dispute_id}

    def _post_dispute_resolve(self, body: dict) -> dict:
        dispute_id = body["id"]
        if not isinstance(dispute_id, int) or isinstance(dispute_id, bool):
            raise ValueError("id must be a whole number")
        with Store(self.server.db_path) as store:
            store.resolve_dispute(dispute_id, _actor(body), _text(body, "note"))
        return {"ok": True}

    def _post_setting(self, body: dict) -> dict:
        with Store(self.server.db_path) as store:
            store.set_setting(_text(body, "key", required=True), _text(body, "value", required=True), _actor(body))
        return {"ok": True}

    def _post_settle(self, body: dict) -> dict:
        raw = body["items"]
        if not isinstance(raw, list) or not all(isinstance(i, dict) for i in raw):
            raise ValueError("items must be a list of {invoice_no, customer}")
        items = [(_text(i, "invoice_no", required=True), _text(i, "customer", required=True)) for i in raw]
        with Store(self.server.db_path) as store:
            total = store.settle_invoices(items, _actor(body), _text(body, "note"))
        return {"settled": len(items), "total": _m(total)}

    def _credit_days(self, value) -> int | None:
        if value in (None, ""):
            return None
        try:
            days = int(value)
        except (TypeError, ValueError):
            raise ValueError("credit_days must be a whole number") from None
        if not 0 <= days <= 365 or isinstance(value, bool):
            raise ValueError("credit_days must be between 0 and 365")
        return days

    def _post_import_preview(self, data: bytes, query: dict) -> dict:
        upload = self.server.uploads.save(data, unquote(self.headers.get("X-Filename", "")))
        try:
            report = import_invoices(upload.path, default_credit_days=self._credit_days(query.get("credit_days")), source=upload.name)
            with Store(self.server.db_path) as store:
                diff = store.diff_import(report)
        except BaseException:
            self.server.uploads.discard(upload.id)
            raise
        return {
            "upload_id": upload.id, "filename": upload.name, "sheet": report.sheet,
            "valid": len(report.invoices), "duplicates": len(report.duplicates), "skipped_rows": len(report.skipped_rows),
            "error_count": len(report.errors), "errors": [{"row": e.row_no, "message": e.message} for e in report.errors[:50]],
            "warnings": report.warnings, "filled_due_dates": len(report.filled_due_dates),
            "diff": {
                "new_count": len(diff.new), "new": [{"invoice_no": n, "customer": c, "amount": _m(a)} for n, c, a in diff.new[:20]],
                "changed_count": len(diff.changed),
                "changed": [{"invoice_no": c.invoice_no, "customer": c.customer, "paid_before": _m(c.paid_before), "paid_after": _m(c.paid_after),
                             "delta": _m(c.paid_delta)} for c in diff.changed[:50]],
                "unchanged": diff.unchanged, "payments_seen": _m(diff.payments_seen),
                "reversals": [{"invoice_no": c.invoice_no, "customer": c.customer, "delta": _m(c.paid_delta)} for c in diff.reversals[:50]],
                "missing_count": len(diff.missing), "missing_total": _m(sum((o for _, _, o in diff.missing), _ZERO)),
                "missing": [{"invoice_no": n, "customer": c, "outstanding": _m(o)} for n, c, o in diff.missing[:200]],
            },
        }

    def _post_import_apply(self, body: dict) -> dict:
        upload = self.server.uploads.get(_text(body, "upload_id", required=True))
        report = import_invoices(upload.path, default_credit_days=self._credit_days(body.get("credit_days")), source=upload.name)
        actor = _actor(body)
        with Store(self.server.db_path) as store:
            diff = store.diff_import(report)
            result = store.apply_import(report, actor, allow_errors=body.get("allow_errors") is True)
            snapshot = store.take_snapshot(date.today(), actor) if body.get("take_snapshot") is True else None
        self.server.uploads.discard(upload.id)
        return {
            "import_id": result.import_id, "inserted": result.inserted, "updated": result.updated, "unchanged": result.unchanged,
            "warnings": result.warnings, "payments_seen": _m(diff.payments_seen),
            "snapshot": None if snapshot is None else {"as_of": snapshot.as_of.isoformat(), "payments_observed": _m(snapshot.payments_observed)},
            "missing": [{"invoice_no": n, "customer": c, "outstanding": _m(o)} for n, c, o in diff.missing[:200]],
            "missing_count": len(diff.missing),
        }

    def _post_statement(self, data: bytes, query: dict) -> dict:
        upload = self.server.uploads.save(data, unquote(self.headers.get("X-Filename", "")))
        try:
            report, last_error = None, None
            for _, rows in read_sheets(upload.path):
                try:
                    report = parse_statement(rows)
                    break
                except StatementError as exc:
                    last_error = exc
            if report is None:
                raise last_error or StatementError("no statement found in that file")
        finally:
            self.server.uploads.discard(upload.id)
        as_of = self._as_of(query)
        with Store(self.server.db_path) as store:
            suggestions = suggest(report.credits[:500], store.invoices(), as_of, store.open_promises())
        return {
            "credits": len(report.credits), "skipped": report.skipped, "errors": report.errors[:20], "truncated": len(report.credits) > 500,
            "suggestions": [{
                "row": s.credit.row_no, "date": None if s.credit.on is None else s.credit.on.isoformat(), "narration": s.credit.narration,
                "reference": s.credit.reference, "amount": _m(s.credit.amount), "confidence": s.confidence, "customer": s.customer,
                "invoice_nos": list(s.invoice_nos), "matched_outstanding": _m(s.matched_outstanding), "reasons": list(s.reasons),
                "promise_ids": list(s.promise_ids)} for s in suggestions],
        }

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
        language = query.get("language") or "en"
        if language not in LANGUAGES:
            return self._error(400, f"language must be one of: {', '.join(LANGUAGES)}")
        sender = Sender(query.get("sender") or "Your name", query.get("company") or "Your company")
        with Store(self.server.db_path) as store:
            draft = draft_for_customer(store.invoices(), store.open_promises(), customer, sender, self._as_of(query), language)
            profile = store.customer_profile(customer)
        blocked = profile.do_not_contact
        self._json(200, {
            "template": draft.template, "subject": draft.subject, "email": draft.email_body, "whatsapp": draft.whatsapp_text, "language": language,
            "contact": {"person": profile.contact_person, "phone": profile.phone, "email": profile.email, "preferred": profile.preferred_channel,
                        "do_not_contact": blocked},
            # links only open the person's own WhatsApp / mail / phone app with the text filled in; nothing is sent from here
            "links": None if blocked else {"whatsapp": whatsapp_link(profile.phone, draft.whatsapp_text),
                                           "email": mailto_link(profile.email, draft.subject, draft.email_body), "tel": tel_link(profile.phone)},
        })

    def _customer(self, query: dict) -> None:
        name = query.get("name", "")
        if not name:
            return self._error(400, "name is required")
        with Store(self.server.db_path) as store:
            self._json(200, customer_detail(store, name, self._as_of(query)))

    def _activity(self, query: dict) -> None:
        try:
            limit = int(query.get("limit", "100"))
            before = int(query["before"]) if query.get("before") else None
        except ValueError:
            return self._error(400, "limit and before must be whole numbers")
        with Store(self.server.db_path) as store:
            self._json(200, {"entries": store.activity(limit, before)})

    def _backup(self) -> None:
        with Store(self.server.db_path) as store, tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "backup.db"
            store.backup(target)
            data = target.read_bytes()
        self._send(200, data, "application/octet-stream",
                   {"Content-Disposition": f'attachment; filename="collectos-backup-{date.today().isoformat()}.db"'})

    def _report(self, query: dict) -> None:
        as_of, company = self._as_of(query), (query.get("company") or "CollectOS")[:80]
        with Store(self.server.db_path) as store, tempfile.TemporaryDirectory() as tmp:
            invoices, promises = store.invoices(), store.open_promises()
            queue = _queue_for_report(store, invoices, as_of, promises)
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
        self.uploads = UploadStore()
        actual = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{actual}", f"localhost:{actual}"}

    def server_close(self) -> None:
        super().server_close()
        self.uploads.close()  # uploaded files never outlive the server

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


def _queue_for_report(store: Store, invoices, as_of: date, promises):
    from .overview import _queue_inputs

    return _queue_inputs(store, as_of)[0]
