"""The read-only endpoints behind the statement, UPI, interest, ship-check, cash-calendar and brief screens."""

import http.client
import json
import threading
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import pytest

from collectos import web
from collectos.importer import import_rows
from collectos.store import Store

HEADER = ["Invoice No", "Customer", "Invoice Date", "Due Date", "Amount", "Paid"]
NOW = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
AS_OF = "2026-10-15"


def make_db(tmp_path):
    path = tmp_path / "t.db"
    rows = [(1, HEADER), (2, ["INV-1042", "Acme Traders", "01-06-2026", "01-07-2026", "100000", "0"]),
            (3, ["INV-1043", "Acme Traders", "01-09-2026", "30-10-2026", "40000", "0"]), (4, ["INV-2001", "Beta Co", "01-08-2026", "01-09-2026", "20000", "0"])]
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
    r = conn.getresponse(); data = r.read(); headers_out = dict(r.getheaders()); conn.close()
    try:
        data = json.loads(data)
    except ValueError:
        pass
    return r.status, data, headers_out


def get(server, path):
    status, data, _ = call(server, path)
    return status, data


def setting(server, key, value):
    token = call(server, "/api/session")[1]["token"]
    status, data, _ = call(server, "/api/setting", "POST", {"key": key, "value": value}, {"Content-Type": "application/json", web.TOKEN_HEADER: token})
    return status, data


def q(**params):
    return "&".join(f"{k}={quote(str(v))}" for k, v in params.items())


# ---- settings reach the page ----------------------------------------------------------------------------------------------

def test_overview_exposes_the_new_settings_with_safe_defaults(srv):
    s = get(srv, f"/api/overview?as_of={AS_OF}")[1]["settings"]
    assert (s["upi_id"], s["payee_name"], s["interest_rate"], s["interest_grace_days"], s["monthly_outgoings"]) == ("", "", "0.00", "0", "0.00")


def test_settings_post_validates(srv):
    assert setting(srv, "upi_id", "shop@okhdfc")[0] == 200
    assert setting(srv, "upi_id", "not a upi id")[0] == 400
    assert setting(srv, "interest_rate", "99")[0] == 400


# ---- statement ----------------------------------------------------------------------------------------------------------------

def test_account_statement_json_and_links(srv):
    status, d = get(srv, "/api/account-statement?" + q(customer="acme traders", as_of=AS_OF, sender="Rohit", company="Sharma Steel"))
    assert status == 200 and d["balance"] == "140000.00" and d["overdue"] == "100000.00" and d["invoice_count"] == 2
    assert "INV-1042" in d["message"] and "Rohit" in d["message"] and "balance receivable" in d["confirmation"]
    assert d["links"]["whatsapp"] is None or d["links"]["whatsapp"].startswith("https://wa.me/")
    assert get(srv, "/api/account-statement?customer=Nobody")[0] == 404
    assert get(srv, "/api/account-statement")[0] == 400


def test_account_statement_do_not_contact_removes_links(srv):
    with Store(srv.db_path, now=lambda: NOW) as s:
        s.update_customer("Acme Traders", "t", do_not_contact=True)
    d = get(srv, "/api/account-statement?" + q(customer="Acme Traders", as_of=AS_OF))[1]
    assert d["links"] is None and d["contact"]["do_not_contact"] is True


def test_statement_workbook_download(srv):
    status, data, headers = call(srv, "/account-statement.xlsx?" + q(customer="Acme Traders", as_of=AS_OF, company="Sharma"))
    assert status == 200 and headers["Content-Type"].startswith("application/vnd.openxmlformats")
    assert 'filename="statement-Acme-Traders-2026-10-15.xlsx"' in headers["Content-Disposition"]
    assert call(srv, "/account-statement.xlsx?customer=Nobody")[0] == 404


# ---- UPI ----------------------------------------------------------------------------------------------------------------------

def test_qr_needs_a_valid_upi_id_and_returns_an_svg_that_the_csp_allows(srv):
    assert call(srv, "/upi-qr.svg?amount=100")[0] == 404
    setting(srv, "upi_id", "shop@okhdfc"); setting(srv, "payee_name", "Sharma Steel")
    status, data, headers = call(srv, "/upi-qr.svg?amount=1500.50&note=INV-1042")
    assert status == 200 and headers["Content-Type"] == "image/svg+xml" and data.startswith(b"<svg")
    assert "img-src 'self'" in headers["Content-Security-Policy"]
    assert call(srv, "/upi-qr.svg?amount=abc")[0] == 400
    assert call(srv, "/upi-qr.svg?amount=0")[0] == 400
    assert call(srv, "/upi-qr.svg")[0] == 200   # an open-amount QR is allowed


def test_draft_carries_upi_details_only_when_configured(srv):
    base = "/api/draft?" + q(customer="Acme Traders", as_of=AS_OF)
    assert get(srv, base)[1]["upi"] is None
    setting(srv, "upi_id", "shop@okhdfc")
    u = get(srv, base)[1]["upi"]
    assert u["id"] == "shop@okhdfc" and u["amount"] == "100000.00" and "shop@okhdfc" in u["text"] and "₹1,00,000.00" in u["text"]
    assert u["qr"] == "/upi-qr.svg?amount=100000.00&note=Acme%20Traders" and "note: Acme Traders" in u["text"]
    hi = get(srv, base + "&language=hinglish")[1]["upi"]["text"]
    assert hi.startswith("UPI se")


# ---- interest -------------------------------------------------------------------------------------------------------------------

def test_interest_is_off_until_configured_then_prices_the_overdue(srv):
    off = get(srv, "/api/interest?" + q(customer="Acme Traders", as_of=AS_OF))[1]
    assert off["configured"] is False
    setting(srv, "interest_rate", "18")
    d = get(srv, "/api/interest?" + q(customer="Acme Traders", as_of=AS_OF, waiver_days=10, sender="Rohit"))[1]
    assert d["configured"] and d["rate"] == "18.00" and d["deadline"] == "2026-10-25" and d["rows"][0]["invoice_no"] == "INV-1042"
    assert d["interest"] == "5227.40" and d["rows"][0]["days_overdue"] == 106
    assert "waive" in d["draft"]["whatsapp"] and "msmed_note" not in d
    assert get(srv, "/api/interest?" + q(customer="Acme Traders", waiver_days="x"))[0] == 400
    assert get(srv, "/api/interest?" + q(customer="Acme Traders", waiver_days=99))[0] == 400


def test_msmed_note_only_for_msme_suppliers(srv):
    setting(srv, "interest_rate", "18"); setting(srv, "msme_supplier", "1")
    assert "MSMED" in get(srv, "/api/interest?" + q(customer="Acme Traders", as_of=AS_OF))[1]["msmed_note"]


# ---- ship check -----------------------------------------------------------------------------------------------------------------

def test_ship_check_endpoint_uses_the_limit_and_history(srv):
    token = call(srv, "/api/session")[1]["token"]
    status = call(srv, "/api/customer/update", "POST", {"customer": "Beta Co", "credit_limit": "25000"}, {"Content-Type": "application/json", web.TOKEN_HEADER: token})[0]
    assert status == 200
    d = get(srv, "/api/shipcheck?" + q(customer="beta co", order="10000", as_of=AS_OF))[1]
    assert d["customer"] == "Beta Co" and d["verdict"] == "advance" and d["advance"] == "5000.00"
    assert d["figures"]["outstanding"] == "20000.00" and d["figures"]["after_order"] == "30000.00"
    acme = get(srv, "/api/shipcheck?" + q(customer="Acme Traders", order="1", as_of=AS_OF))[1]
    assert acme["verdict"] == "hold" and any("overdue" in r["text"] for r in acme["reasons"])
    assert get(srv, "/api/shipcheck?" + q(customer="Stranger", order="500"))[1]["verdict"] == "new_customer"
    assert get(srv, "/api/shipcheck?" + q(customer="Beta Co", order="lots"))[0] == 400
    assert get(srv, "/api/shipcheck?" + q(customer="Beta Co", order="0"))[0] == 400
    assert get(srv, "/api/shipcheck?" + q(order="5"))[0] == 400


# ---- cash calendar and brief ----------------------------------------------------------------------------------------------------

def test_cash_calendar_endpoint_counts_dated_money_only(srv):
    setting(srv, "monthly_outgoings", "50000")
    d = get(srv, "/api/cashcal?" + q(as_of=AS_OF))[1]
    assert len(d["weeks"]) == 5 and d["outgoings"]["monthly"] == "50000.00"
    assert d["totals"]["all_30"] == "40000.00"            # INV-1043 due 30-Oct; the overdue ones have no promise date
    assert d["undated"]["customers"] == 2 and "planning view" in d["note"]


def test_brief_endpoint(srv):
    d = get(srv, "/api/brief?" + q(as_of=AS_OF, company="Sharma Steel"))[1]
    assert d["text"].startswith("Sharma Steel Morning brief") and "Call today:" in d["text"] and d["facts"]["to_call"] == 2


def test_new_get_routes_are_read_only_and_leave_no_audit_trail(srv):
    with Store(srv.db_path, now=lambda: NOW) as s:
        before = s.activity(500)
    for path in ("/api/brief", "/api/cashcal", "/api/shipcheck?customer=Beta+Co&order=5", "/api/interest?customer=Beta+Co", "/api/account-statement?customer=Beta+Co",
                 "/account-statement.xlsx?customer=Beta+Co", "/upi-qr.svg"):
        call(srv, path)
    with Store(srv.db_path, now=lambda: NOW) as s:
        assert s.activity(500) == before
    # none of them can be reached by POST: writes stay a fixed list
    for path in ("/api/brief", "/api/cashcal", "/api/shipcheck", "/api/interest", "/api/account-statement", "/upi-qr.svg"):
        assert call(srv, path, "POST", {}, {"Content-Type": "application/json"})[0] == 405
