import http.client
import json
import threading
from datetime import date, datetime, timezone
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from collectos import web
from collectos.importer import import_rows
from collectos.store import Store

HEADER = ["Invoice No", "Customer", "Invoice Date", "Due Date", "Amount", "Paid"]
NOW = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
TODAY = date.today()


class FakeClient:
    def __init__(self, *answers):
        self.answers, self.calls, self.messages = list(answers), [], self

    def create(self, **kw):
        self.calls.append(kw)
        item = self.answers.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(item))], usage=SimpleNamespace(input_tokens=500, output_tokens=100), stop_reason="end_turn")


def make_db(tmp_path):
    path = tmp_path / "t.db"
    rows = [(1, HEADER), (2, ["INV-1042", "Acme Traders", "01-08-2026", "01-09-2026", "50000", "0"]), (3, ["INV-1043", "Acme Traders", "01-08-2026", "01-09-2026", "50000", "0"]),
            (4, ["INV-2001", "Beta Co", "01-08-2026", "01-09-2026", "20000", "0"])]
    with Store(path, now=lambda: NOW) as s:
        s.apply_import(import_rows(rows), actor="t")
    return path


def start(tmp_path, *answers, ai=True, key=True):
    client = FakeClient(*answers) if key else None
    srv = web.make_server(make_db(tmp_path), ai_client=client)
    threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.02), daemon=True).start()
    if ai:
        with Store(srv.db_path) as s:
            s.set_setting("ai_assist", "1", "t")
    return srv, client


@pytest.fixture
def stop():
    servers = []
    yield servers.append
    for s in servers:
        s.shutdown(); s.server_close()


def call(srv, path, method="GET", body=None, headers=None, raw=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=15)
    payload = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    conn.putrequest(method, path, skip_host=True)
    conn.putheader("Host", f"127.0.0.1:{srv.server_address[1]}")
    for k, v in (headers or {}).items():
        conn.putheader(k, v)
    if payload is not None:
        conn.putheader("Content-Length", str(len(payload)))
    conn.endheaders(payload)
    r = conn.getresponse(); data = r.read(); conn.close()
    try:
        data = json.loads(data)
    except ValueError:
        pass
    return r.status, data


def token(srv):
    return call(srv, "/api/session")[1]["token"]


def post(srv, path, body):
    return call(srv, path, "POST", body, {"Content-Type": "application/json", web.TOKEN_HEADER: token(srv)})


def upload(srv, path, data, filename="x.csv"):
    return call(srv, path, "POST", None, {"Content-Type": "application/octet-stream", web.TOKEN_HEADER: token(srv), "X-Filename": quote(filename)}, raw=data)


def reply(**over):
    base = {"intent": "promise_to_pay", "promise_date": TODAY.replace(year=TODAY.year + 1).isoformat(), "promise_amount": "50000", "dispute_category": "none",
            "quote": "Friday tak 50 hazaar", "summary": "Said he will pay Rs 50,000 on Friday", "confidence": "high"}
    base.update(over)
    return base


ODD = b"Reference,Ledger,Billed on,Pay by,Total\nINV-7,Gamma Ltd,01-09-2026,01-10-2026,5000\nINV-8,Delta Co,02-09-2026,02-10-2026,6000\n"
ODD_MAP = {"header_row": 1, "columns": {"invoice_no": 1, "customer": 2, "invoice_date": 3, "due_date": 4, "amount": 5, "paid": 0, "outstanding": 0}, "notes": "ok", "confidence": "high"}


# ---- status and gating ---------------------------------------------------------------------------------------------------


def test_status_when_everything_is_off(tmp_path, stop):
    srv, _ = start(tmp_path, ai=False, key=False)
    stop(srv)
    s = call(srv, "/api/ai/status")[1]
    assert s["enabled"] is False and s["key_present"] is False and s["model"] == "claude-haiku-5-5" and s["budget_usd"] == "2.00" and s["calls"] == 0


def test_status_when_on(tmp_path, stop):
    srv, _ = start(tmp_path, reply())
    stop(srv)
    assert call(srv, "/api/ai/status")[1]["enabled"] is True and call(srv, "/api/ai/status")[1]["key_present"] is True


def test_every_ai_route_refuses_politely_when_switched_off(tmp_path, stop):
    srv, client = start(tmp_path, reply(), ai=False)
    stop(srv)
    status, out = post(srv, "/api/ai/reply", {"text": "Friday tak 50 hazaar"})
    assert status == 409 and "switched off" in out["error"] and client.calls == []
    assert post(srv, "/api/ai/columns", {"upload_id": "x" * 22})[0] in (400, 409)


def test_no_key_gives_a_clear_message(tmp_path, stop):
    srv, _ = start(tmp_path, key=False)
    stop(srv)
    status, out = post(srv, "/api/ai/reply", {"text": "Friday tak 50 hazaar"})
    assert status == 409 and "ANTHROPIC_API_KEY" in out["error"]


# ---- reply reader -----------------------------------------------------------------------------------------------------------


def test_reply_reader_returns_a_checked_suggestion_and_logs_the_call(tmp_path, stop):
    srv, client = start(tmp_path, reply())
    stop(srv)
    status, out = post(srv, "/api/ai/reply", {"text": "Bhai Friday tak 50 hazaar bhej dunga, call 98765 43210", "actor": "priya"})
    assert status == 200 and out["intent"] == "promise_to_pay" and out["promise_amount"] == "50000.00" and out["masked"] == {"phone": 1}
    assert "98765" not in client.calls[0]["messages"][0]["content"]
    with Store(srv.db_path) as s:
        assert s.ai_month_summary()["calls"] == 1
        assert [e for e in s.audit_entries() if e.action == "ai.request"][0].actor == "priya"


def test_reply_reader_input_errors_are_400_not_502(tmp_path, stop):
    srv, client = start(tmp_path, reply())
    stop(srv)
    assert post(srv, "/api/ai/reply", {"text": "   "})[0] == 400
    assert post(srv, "/api/ai/reply", {"text": "x" * 3000})[0] == 400
    assert post(srv, "/api/ai/reply", {})[0] == 400
    assert client.calls == []


def test_a_failing_ai_service_is_a_502_with_a_safe_message(tmp_path, stop):
    boom = type("RateLimitError", (Exception,), {})("internal detail")
    srv, _ = start(tmp_path, boom)
    stop(srv)
    status, out = post(srv, "/api/ai/reply", {"text": "Friday tak 50 hazaar"})
    assert status == 502 and "busy" in out["error"] and "internal detail" not in out["error"]


def test_bad_model_output_is_a_502(tmp_path, stop):
    srv, _ = start(tmp_path, {"unexpected": "shape"})
    stop(srv)
    assert post(srv, "/api/ai/reply", {"text": "Friday tak 50 hazaar"})[0] == 502


def test_the_cap_blocks_the_next_request(tmp_path, stop):
    srv, client = start(tmp_path, reply(), reply())
    stop(srv)
    post(srv, "/api/setting", {"key": "ai_budget_usd", "value": "0.00"})
    status, out = post(srv, "/api/ai/reply", {"text": "Friday tak 50 hazaar"})
    assert status == 409 and "spending cap" in out["error"] and client.calls == []


# ---- column mapping: by hand, or with the helper ---------------------------------------------------------------------------------------------


def preview_odd(srv):
    s, p = upload(srv, "/api/import/preview", ODD, filename="weird export.csv")
    assert s == 200 and p["needs_mapping"] is True
    return p


def test_layout_shows_the_top_of_the_file_and_guesses_the_header(tmp_path, stop):
    srv, _ = start(tmp_path, ai=False)
    stop(srv)
    p = preview_odd(srv)
    s, layout = post(srv, "/api/import/layout", {"upload_id": p["upload_id"]})
    assert s == 200 and layout["guess"] == {"sheet": None, "header_row": 1} and layout["fields"][:3] == ["invoice_no", "customer", "invoice_date"]
    assert layout["sheets"][0]["rows"][0]["cells"][:2] == ["Reference", "Ledger"] and layout["sheets"][0]["rows"][1]["cells"][1] == "Gamma Ltd"


def test_manual_mapping_works_with_no_ai_at_all(tmp_path, stop):
    srv, client = start(tmp_path, ai=False, key=False)
    stop(srv)
    p = preview_odd(srv)
    mapping = {"upload_id": p["upload_id"], "header_row": 1, "columns": ODD_MAP["columns"]}
    s, preview = post(srv, "/api/import/preview-mapped", mapping)
    assert s == 200 and preview["valid"] == 2 and preview["error_count"] == 0 and preview["diff"]["new_count"] == 2
    assert preview["mapping"]["columns"]["customer"] == 2 and preview["upload_id"] == p["upload_id"]
    s, out = post(srv, "/api/import/apply", {"upload_id": p["upload_id"], "mapping": {"header_row": 1, "columns": ODD_MAP["columns"]}})
    assert s == 200 and out["inserted"] == 2
    with Store(srv.db_path) as st:
        assert {i.invoice_no for i in st.invoices()} >= {"INV-7", "INV-8"}
    assert not any(srv.uploads.directory.iterdir())


def test_apply_without_the_mapping_fails_cleanly_for_such_a_file(tmp_path, stop):
    srv, _ = start(tmp_path, ai=False)
    stop(srv)
    p = preview_odd(srv)
    s, out = post(srv, "/api/import/apply", {"upload_id": p["upload_id"]})
    assert s == 400 and "could not find an invoice table" in out["error"]


@pytest.mark.parametrize("mapping, fragment", [
    ({"header_row": 1, "columns": {"invoice_no": 1}}, "needs invoice number"),
    ({"header_row": 1, "columns": {"invoice_no": 1, "customer": 1, "invoice_date": 3, "amount": 5}}, "same column"),
    ({"header_row": 99, "columns": ODD_MAP["columns"]}, "not in the sheet"),
    ({"header_row": "1", "columns": ODD_MAP["columns"]}, "malformed"),
    ({"header_row": 1, "columns": {**ODD_MAP["columns"], "bogus": 2}}, "unknown field"),
    ({"header_row": 1, "columns": {**ODD_MAP["columns"], "customer": -3}}, "whole numbers"),
    ({"header_row": 1}, "mapping is missing"),
])
def test_bad_mappings_are_400s(tmp_path, stop, mapping, fragment):
    srv, _ = start(tmp_path, ai=False)
    stop(srv)
    p = preview_odd(srv)
    s, out = post(srv, "/api/import/preview-mapped", {"upload_id": p["upload_id"], **mapping})
    assert s == 400 and fragment in out["error"]


def test_a_wrong_mapping_produces_row_errors_and_saves_nothing_by_itself(tmp_path, stop):
    srv, _ = start(tmp_path, ai=False)
    stop(srv)
    p = preview_odd(srv)
    swapped = {"invoice_no": 1, "customer": 2, "invoice_date": 5, "due_date": 4, "amount": 3}
    s, preview = post(srv, "/api/import/preview-mapped", {"upload_id": p["upload_id"], "header_row": 1, "columns": swapped})
    assert s == 200 and preview["valid"] == 0 and preview["error_count"] == 2
    with Store(srv.db_path) as st:
        assert len(st.invoices()) == 3                                   # the three that were already there; nothing added


def test_ai_column_helper_suggests_a_mapping_that_then_imports(tmp_path, stop):
    srv, client = start(tmp_path, ODD_MAP)
    stop(srv)
    p = preview_odd(srv)
    assert p["ai_available"] is True
    s, sug = post(srv, "/api/ai/columns", {"upload_id": p["upload_id"]})
    assert s == 200 and sug["header_row"] == 1 and sug["columns"]["customer"] == 2
    sent = client.calls[0]["messages"][0]["content"]
    assert "Gamma Ltd" not in sent and "5000" not in sent and "Reference" in sent
    s, preview = post(srv, "/api/import/preview-mapped", {"upload_id": p["upload_id"], "sheet": sug["sheet"], "header_row": sug["header_row"], "columns": sug["columns"]})
    assert s == 200 and preview["valid"] == 2


def test_ai_column_helper_cannot_be_trusted_blindly(tmp_path, stop):
    srv, _ = start(tmp_path, {**ODD_MAP, "columns": {**ODD_MAP["columns"], "customer": 40}})
    stop(srv)
    p = preview_odd(srv)
    s, out = post(srv, "/api/ai/columns", {"upload_id": p["upload_id"]})
    assert s == 502 and "not a column" in out["error"]


def test_unknown_upload_ids_are_refused(tmp_path, stop):
    srv, _ = start(tmp_path, ai=False)
    stop(srv)
    for path in ("/api/import/layout", "/api/import/preview-mapped"):
        assert post(srv, path, {"upload_id": "A" * 22, "header_row": 1, "columns": {}})[0] == 400
    assert post(srv, "/api/import/layout", {"upload_id": "../../etc/passwd"})[0] == 400


# ---- remittance reader ---------------------------------------------------------------------------------------------------------------------------


def remit(**over):
    base = {"payer": "Acme Traders Pvt Ltd", "payment_date": "2026-10-03", "reference": "UTR123", "total_received": "98000", "total_tds": "2000",
            "invoices": [{"invoice_no": "INV-1042", "gross": "50000", "tds": "1000", "net": "49000"}, {"invoice_no": "inv 1043", "gross": "50000", "tds": "1000", "net": "49000"},
                         {"invoice_no": "INV-9999", "gross": "100", "tds": "", "net": "100"}], "notes": "none", "confidence": "high"}
    base.update(over)
    return base


def test_remittance_is_read_and_matched_to_our_invoices_without_changing_them(tmp_path, stop):
    srv, client = start(tmp_path, remit())
    stop(srv)
    s, out = upload(srv, "/api/ai/remittance", b"%PDF-1.4 fake pdf", filename="advice.pdf")
    assert s == 200 and out["total_tds"] == "2000.00"
    m = {x["invoice_no"]: x for x in out["matches"]}
    assert m["INV-1042"]["found"] and m["INV-1042"]["customer"] == "Acme Traders" and m["INV-1042"]["outstanding"] == "50000.00"
    assert m["INV-1042"]["settles"] is True                       # net 49,000 + TDS 1,000 = the 50,000 outstanding
    assert m["INV-1043"]["found"]                                 # "inv 1043" matches "INV-1043" ignoring punctuation and case
    assert m["INV-9999"]["found"] is False and "not found" in m["INV-9999"]["notes"][0]
    assert client.calls[0]["messages"][0]["content"][0]["type"] == "document"
    with Store(srv.db_path) as st:
        assert all(i.paid == 0 for i in st.invoices())            # nothing changed
    assert not any(srv.uploads.directory.iterdir())               # never written to disk


def test_remittance_type_comes_from_the_bytes_not_the_filename(tmp_path, stop):
    srv, client = start(tmp_path, remit())
    stop(srv)
    s, out = upload(srv, "/api/ai/remittance", b"<html><script>alert(1)</script></html>", filename="advice.pdf")
    assert s == 400 and "PDF or a PNG" in out["error"] and client.calls == []
    assert upload(srv, "/api/ai/remittance", b"\x89PNG\r\n\x1a\nxxxx", filename="photo.txt")[0] == 200
    assert client.calls[0]["messages"][0]["content"][0]["type"] == "image"


def test_remittance_matching_flags_ambiguity_and_gross_mismatch(tmp_path, stop):
    srv, _ = start(tmp_path, remit(invoices=[{"invoice_no": "INV-1042", "gross": "49000", "tds": "1000", "net": "48000"}]))
    stop(srv)
    s, out = upload(srv, "/api/ai/remittance", b"%PDF-1.4 fake", filename="a.pdf")
    note = out["matches"][0]["notes"][0]
    assert s == 200 and "gross 49000.00 but we show 50000.00" in note


def test_a_remittance_that_does_not_add_up_is_not_called_settling(tmp_path, stop):
    srv, _ = start(tmp_path, remit(invoices=[{"invoice_no": "INV-1042", "gross": "50000", "tds": "1000", "net": "48000"}]))
    stop(srv)
    out = upload(srv, "/api/ai/remittance", b"%PDF-1.4 fake", filename="a.pdf")[1]
    assert out["matches"][0]["settles"] is False and "net plus TDS does not equal the gross" in " ".join(out["matches"][0]["notes"])


def test_a_remittance_that_exactly_settles_an_invoice_is_pointed_out(tmp_path, stop):
    srv, _ = start(tmp_path, remit(invoices=[{"invoice_no": "INV-1042", "gross": "50000", "tds": "1000", "net": "49000"}]))
    stop(srv)
    out = upload(srv, "/api/ai/remittance", b"%PDF-1.4 fake", filename="a.pdf")[1]
    assert out["matches"][0]["settles"] is True                   # net 49,000 + TDS 1,000 = 50,000, exactly the outstanding
