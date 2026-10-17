"""Endpoints for cheques, the TDS register, insights, the GST 180-day flag, groups and the MSMED estimate."""

import http.client
import json
import threading
from datetime import datetime, timezone
from urllib.parse import quote

import pytest

from collectos import web
from collectos.importer import import_rows
from collectos.store import Store

HEADER = ["Invoice No", "Customer", "Invoice Date", "Due Date", "Amount", "Paid"]
NOW = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
AS_OF = "2026-10-15"
JSON_H = {"Content-Type": "application/json"}


def make_db(tmp_path):
    path = tmp_path / "t.db"
    rows = [(1, HEADER), (2, ["INV-1042", "Acme Traders", "01-06-2026", "01-07-2026", "100000", "0"]), (3, ["INV-1043", "Acme Traders", "01-09-2026", "30-10-2026", "40000", "0"]),
            (4, ["INV-0900", "Acme Traders", "01-04-2026", "01-05-2026", "5000", "0"]), (5, ["INV-2001", "Beta Co", "01-08-2026", "01-09-2026", "20000", "0"])]
    with Store(path, now=lambda: NOW) as s:
        s.apply_import(import_rows(rows), actor="t")
    return path


@pytest.fixture
def srv(tmp_path):
    server = web.make_server(make_db(tmp_path))
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True).start()
    yield server
    server.shutdown(); server.server_close()


def call(server, path, method="GET", body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=15)
    payload = None if body is None else json.dumps(body).encode()
    conn.putrequest(method, path, skip_host=True)
    conn.putheader("Host", f"127.0.0.1:{server.server_address[1]}")
    for k, v in (headers or {}).items():
        conn.putheader(k, v)
    if payload is not None:
        conn.putheader("Content-Length", str(len(payload)))
    conn.endheaders(payload)
    r = conn.getresponse(); data = r.read(); h = {k.lower(): v for k, v in r.getheaders()}; conn.close()
    try:
        data = json.loads(data)
    except ValueError:
        pass
    return r.status, data, h


def get(server, path):
    s, d, _ = call(server, path)
    return s, d


def post(server, path, body):
    token = call(server, "/api/session")[1]["token"]
    s, d, _ = call(server, path, "POST", body, {**JSON_H, web.TOKEN_HEADER: token})
    return s, d


def q(**p):
    return "&".join(f"{k}={quote(str(v))}" for k, v in p.items())


def add_cheque(srv, **over):
    base = {"customer": "Acme Traders", "cheque_no": "000123", "amount": "30000", "cheque_date": "2026-10-17", "bank": "HDFC", "invoice_nos": "INV-1042"}
    return post(srv, "/api/cheque/add", {**base, **over})


# ---- cheques ----------------------------------------------------------------------------------------------------------------------

def test_cheque_register_flow_with_summary_and_allowed_next_steps(srv):
    s, d = add_cheque(srv)
    assert s == 200
    cid = d["id"]
    listing = get(srv, "/api/cheques?" + q(as_of=AS_OF))[1]
    c = listing["cheques"][0]
    assert (c["customer"], c["amount"], c["status"], c["days_to_date"], c["next"]) == ("Acme Traders", "30000.00", "received", 2, ["deposited", "returned"])
    assert listing["summary"]["in_hand"] == "30000.00" and listing["summary"]["due_soon_count"] == 1 and "Acme Traders" in listing["customers"]
    assert post(srv, "/api/cheque/status", {"id": cid, "status": "cleared"})[0] == 400              # cannot skip the deposit step
    assert post(srv, "/api/cheque/status", {"id": cid, "status": "deposited"})[0] == 200
    assert post(srv, "/api/cheque/status", {"id": cid, "status": "bounced", "note": "funds"})[0] == 200
    after = get(srv, "/api/cheques?" + q(as_of=AS_OF))[1]
    assert after["summary"]["bounced_count"] == 1 and after["summary"]["bounced"] == "30000.00" and after["summary"]["in_hand"] == "0.00"
    assert get(srv, "/api/cheques?status=bounced")[1]["cheques"][0]["note"] == "funds" and get(srv, "/api/cheques?status=cleared")[1]["cheques"] == []


@pytest.mark.parametrize("bad", [{"customer": "Nobody"}, {"amount": "0"}, {"amount": "abc"}, {"cheque_date": "not a date"}, {"cheque_no": ""}, {"bank": "b" * 61}])
def test_cheque_add_rejects_bad_input(srv, bad):
    assert add_cheque(srv, **bad)[0] == 400


def test_cheque_status_validates_its_input(srv):
    assert post(srv, "/api/cheque/status", {"id": 999, "status": "deposited"})[0] == 400
    assert post(srv, "/api/cheque/status", {"id": "1", "status": "deposited"})[0] == 400
    assert post(srv, "/api/cheque/status", {"id": True, "status": "deposited"})[0] == 400
    assert add_cheque(srv)[0] == 200 and add_cheque(srv)[0] == 400                                     # duplicate number for the same customer and bank


def test_cheques_reach_the_briefing_the_calendar_and_the_customer_page(srv):
    cid = add_cheque(srv, cheque_date="2026-10-16")[1]["id"]
    ov = get(srv, f"/api/overview?as_of={AS_OF}")[1]
    assert ov["kpi"]["cheques_due_soon"] == 1 and any(b["kind"] == "cheques" and "ready to deposit" in b["text"] for b in ov["briefing"])
    cal = get(srv, f"/api/cashcal?as_of={AS_OF}")[1]
    assert any(i["kind"] == "cheque" and i["amount"] == "30000.00" for w in cal["weeks"] for i in w["items"])
    assert get(srv, "/api/customer?" + q(name="Acme Traders", as_of=AS_OF))[1]["cheques"][0]["cheque_no"] == "000123"
    post(srv, "/api/cheque/status", {"id": cid, "status": "deposited"}); post(srv, "/api/cheque/status", {"id": cid, "status": "bounced"})
    ov = get(srv, f"/api/overview?as_of={AS_OF}")[1]
    assert ov["kpi"]["cheques_bounced"] == 1 and any(b["kind"] == "bounced" and b["level"] == "warn" for b in ov["briefing"])


def test_a_cheque_does_not_reduce_what_the_customer_owes(srv):
    before = get(srv, f"/api/overview?as_of={AS_OF}")[1]["kpi"]["total_outstanding"]
    cid = add_cheque(srv)[1]["id"]
    post(srv, "/api/cheque/status", {"id": cid, "status": "deposited"}); post(srv, "/api/cheque/status", {"id": cid, "status": "cleared"})
    assert get(srv, f"/api/overview?as_of={AS_OF}")[1]["kpi"]["total_outstanding"] == before


# ---- TDS register -----------------------------------------------------------------------------------------------------------------

def test_tds_register_by_quarter_with_26as_tick_and_void(srv):
    t1 = post(srv, "/api/tds/add", {"customer": "Acme Traders", "deducted_on": "2026-10-05", "tds": "1224", "invoice_no": "INV-1042", "gross": "61200"})[1]["id"]
    post(srv, "/api/tds/add", {"customer": "Beta Co", "deducted_on": "2026-07-10", "tds": "300"})
    d = get(srv, "/api/tds?" + q(as_of=AS_OF))[1]
    assert d["fy"] == "2026-27" and d["total"] == "1524.00" and "2026-27" in d["years"]
    q3 = d["quarters"][2]
    assert (q3["quarter"], q3["total"], q3["count"], q3["not_in_26as"]) == (3, "1224.00", 1, "1224.00") and d["quarters"][1]["total"] == "300.00"
    assert post(srv, "/api/tds/26as", {"id": t1, "seen": True})[0] == 200
    assert get(srv, "/api/tds?" + q(as_of=AS_OF))[1]["quarters"][2]["not_in_26as"] == "0.00"
    assert post(srv, "/api/tds/26as", {"id": t1, "seen": "yes"})[0] == 400
    assert post(srv, "/api/tds/void", {"id": t1, "note": "twice"})[0] == 200
    assert get(srv, "/api/tds?" + q(as_of=AS_OF))[1]["total"] == "300.00"


@pytest.mark.parametrize("bad", [{"tds": "0"}, {"tds": "x"}, {"customer": "Nobody"}, {"deducted_on": "bad"}, {"gross": "10", "tds": "20"}])
def test_tds_add_rejects_bad_input(srv, bad):
    assert post(srv, "/api/tds/add", {"customer": "Acme Traders", "deducted_on": "2026-10-05", "tds": "100", **bad})[0] == 400


def test_tds_year_filter_and_validation(srv):
    post(srv, "/api/tds/add", {"customer": "Acme Traders", "deducted_on": "2026-03-31", "tds": "10"})
    assert get(srv, "/api/tds?fy=2025-26")[1]["total"] == "10.00" and get(srv, "/api/tds?fy=2026-27")[1]["total"] == "0.00"
    assert get(srv, "/api/tds?fy=2026-99")[0] == 400


def test_tds_csv_is_a_download_and_text_cannot_run_as_a_formula(srv):
    post(srv, "/api/tds/add", {"customer": "Acme Traders", "deducted_on": "2026-10-05", "tds": "1224.50", "invoice_no": "=HYPERLINK(\"http://x\")", "note": "+cmd"})
    s, data, h = call(srv, "/tds.csv?" + q(fy="2026-27"))
    text = data.decode("utf-8-sig") if isinstance(data, bytes) else data
    assert s == 200 and h["content-type"].startswith("text/csv") and 'filename="tds-register-2026-27.csv"' in h["content-disposition"]
    assert "Financial year,Quarter" in text and "1224.50" in text and "'=HYPERLINK" in text and ",'+cmd" in text and ",=HYPERLINK" not in text
    assert call(srv, "/tds.csv?fy=bad")[0] == 400


# ---- insights, groups, the 180-day flag, the MSMED estimate --------------------------------------------------------------------

def test_insights_bundle_and_health_reaches_the_dashboard(srv):
    d = get(srv, "/api/insights?" + q(as_of=AS_OF))[1]
    assert set(d) == {"as_of", "dormant", "groups", "health", "itc"} and d["itc"] == {"enabled": False, "flags": [], "caution": d["itc"]["caution"]}
    assert 0 <= d["health"]["score"] <= 100 and {i["key"] for i in d["health"]["items"]} >= {"no_due_date", "stale", "no_contact"}
    assert get(srv, f"/api/overview?as_of={AS_OF}")[1]["health"]["score"] == d["health"]["score"]


def test_gst_180_day_flag_is_off_until_enabled_then_marks_invoices_and_adds_a_reminder_note(srv):
    assert get(srv, f"/api/overview?as_of={AS_OF}")[1]["kpi"]["itc_flagged"] == 0
    assert "gst180" in get(srv, "/api/draft?" + q(customer="Acme Traders", as_of=AS_OF))[1] and get(srv, "/api/draft?" + q(customer="Acme Traders", as_of=AS_OF))[1]["gst180"] is None
    assert post(srv, "/api/setting", {"key": "itc_flag", "value": "1"})[0] == 200
    ov = get(srv, f"/api/overview?as_of={AS_OF}")[1]
    assert ov["kpi"]["itc_flagged"] == 1 and any(b["kind"] == "itc" for b in ov["briefing"]) and ov["settings"]["itc_flag"] == "1"
    note = get(srv, "/api/draft?" + q(customer="Acme Traders", as_of=AS_OF))[1]["gst180"]["text"]
    assert "Rule 37" in note and "INV-0900 (197 days)" in note
    assert get(srv, "/api/draft?" + q(customer="Beta Co", as_of=AS_OF))[1]["gst180"] is None
    flags = get(srv, "/api/insights?" + q(as_of=AS_OF))[1]["itc"]["flags"]
    assert [(f["invoice_no"], f["state"]) for f in flags] == [("INV-0900", "past")]
    inv = {i["invoice_no"]: i["itc"] for i in get(srv, "/api/customer?" + q(name="Acme Traders", as_of=AS_OF))[1]["invoices"]}
    assert inv["INV-0900"] == "past" and inv["INV-1042"] is None
    assert post(srv, "/api/setting", {"key": "itc_flag", "value": "2"})[0] == 400


def test_groups_flow_into_insights_and_the_ship_check(srv):
    assert post(srv, "/api/customer/update", {"customer": "Acme Traders", "group": "Gupta Group", "credit_limit": "100000"})[0] == 200
    assert post(srv, "/api/customer/update", {"customer": "Beta Co", "group": "Gupta Group", "credit_limit": "50000"})[0] == 200
    g = get(srv, "/api/insights?" + q(as_of=AS_OF))[1]["groups"]["groups"][0]
    assert g["group"] == "Gupta Group" and g["combined_limit"] == "150000.00" and g["overdue"] == "125000.00" and g["over_limit"] is True
    assert get(srv, "/api/customer?" + q(name="Beta Co", as_of=AS_OF))[1]["profile"]["group"] == "Gupta Group"
    r = get(srv, "/api/shipcheck?" + q(customer="Beta Co", order="100", as_of=AS_OF))[1]
    assert any("Gupta Group" in x["text"] and "Acme Traders" in x["text"] for x in r["reasons"])


def test_dormant_customers_appear_in_insights(tmp_path):
    rows = [(1, HEADER)] + [(n + 2, [f"Q-{n}", "Quiet Co", f"01-0{m}-2026", f"28-0{m}-2026", "1000", "1000"]) for n, m in enumerate((2, 3, 4, 5))]
    path = tmp_path / "d.db"
    with Store(path, now=lambda: NOW) as s:
        s.apply_import(import_rows(rows), actor="t")
    server = web.make_server(path)
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True).start()
    try:
        d = get(server, "/api/insights?" + q(as_of=AS_OF))[1]["dormant"]
        assert [r["customer"] for r in d] == ["Quiet Co"]
        assert any(b["kind"] == "dormant" for b in get(server, f"/api/overview?as_of={AS_OF}")[1]["briefing"])
    finally:
        server.shutdown(); server.server_close()


def test_msmed_estimate_appears_for_msme_suppliers_with_a_bank_rate(srv):
    post(srv, "/api/setting", {"key": "msme_supplier", "value": "1"})
    assert get(srv, "/api/interest?" + q(customer="Acme Traders", as_of=AS_OF))[1]["msmed"] is None
    assert post(srv, "/api/setting", {"key": "msmed_bank_rate", "value": "6.5"})[0] == 200
    d = get(srv, "/api/interest?" + q(customer="Acme Traders", as_of=AS_OF))[1]
    assert d["msmed"]["rate"] == "19.50" and {r["invoice_no"] for r in d["msmed"]["rows"]} >= {"INV-1042", "INV-0900"} and "estimate" in d["msmed"]["note"]
    assert post(srv, "/api/setting", {"key": "msmed_bank_rate", "value": "99"})[0] == 400
    post(srv, "/api/setting", {"key": "msme_supplier", "value": "0"})
    assert "msmed" not in get(srv, "/api/interest?" + q(customer="Acme Traders", as_of=AS_OF))[1]


# ---- the guard rails apply to the new routes too -----------------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/api/cheque/add", "/api/cheque/status", "/api/tds/add", "/api/tds/26as", "/api/tds/void"])
def test_new_writes_need_the_session_token_and_are_off_in_read_only_mode(tmp_path, path):
    (tmp_path / "ro").mkdir()
    server = web.make_server(make_db(tmp_path))
    ro = web.make_server(make_db(tmp_path / "ro"), read_only=True)
    for s in (server, ro):
        threading.Thread(target=lambda s=s: s.serve_forever(poll_interval=0.02), daemon=True).start()
    try:
        assert call(server, path, "POST", {}, JSON_H)[0] == 403                                    # no token
        assert call(ro, path, "POST", {}, {**JSON_H, web.TOKEN_HEADER: ro.token})[0] == 405          # read-only
    finally:
        for s in (server, ro):
            s.shutdown(); s.server_close()
