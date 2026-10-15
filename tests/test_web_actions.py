import http.client
import json
import threading
from datetime import date, datetime, timedelta, timezone

import pytest

from collectos import web
from collectos.importer import import_rows
from collectos.store import Store

HEADER = ["Invoice No", "Customer", "Invoice Date", "Due Date", "Amount", "Paid"]
NOW = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
FUTURE = (date.today() + timedelta(days=5)).isoformat()


def make_db(tmp_path):
    path = tmp_path / "t.db"
    rows = [(1, HEADER), (2, ["A1", "Acme Traders", "01-06-2026", "01-07-2026", "100000", "0"]),
            (3, ["B1", "Beta Co", "01-09-2026", "01-10-2026", "20000", "0"])]
    with Store(path, now=lambda: NOW) as s:
        s.apply_import(import_rows(rows), actor="t")
    return path


def start(db, read_only=False):
    srv = web.make_server(db, read_only=read_only)
    threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.02), daemon=True).start()
    return srv


@pytest.fixture
def server(tmp_path):
    srv = start(make_db(tmp_path))
    yield srv
    srv.shutdown(); srv.server_close()


def call(srv, path, method="GET", body=None, headers=None, raw=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    h = {"Host": host or f"127.0.0.1:{srv.server_address[1]}", **(headers or {})}
    payload = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    conn.putrequest(method, path, skip_host=True)
    for k, v in h.items():
        conn.putheader(k, v)
    if payload is not None and "Content-Length" not in h:
        conn.putheader("Content-Length", str(len(payload)))
    conn.endheaders(payload)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    try:
        parsed = json.loads(data)
    except ValueError:
        parsed = data
    return resp.status, {k.lower(): v for k, v in resp.getheaders()}, parsed


def token(srv):
    return call(srv, "/api/session")[2]["token"]


def post(srv, path, body, tok=None, extra=None, **kw):
    headers = {"Content-Type": "application/json", web.TOKEN_HEADER: token(srv) if tok is None else tok, **(extra or {})}
    return call(srv, path, "POST", body, headers, **kw)


def overview(srv):
    return call(srv, "/api/overview?as_of=2026-10-15")[2]


FOLLOWUP = {"customer": "acme traders", "channel": "call", "outcome": "Will pay Friday", "note": "spoke to accounts"}


# ---- happy paths -----------------------------------------------------------------------


def test_session_endpoint(server):
    status, headers, body = call(server, "/api/session")
    assert status == 200 and body["writable"] is True and len(body["token"]) >= 32
    assert "access-control-allow-origin" not in headers and headers["cross-origin-resource-policy"] == "same-origin"


def test_log_followup_with_promise_changes_the_queue(server, tmp_path):
    assert [q["customer"] for q in overview(server)["queue"]] == ["Acme Traders", "Beta Co"]
    status, _, body = post(server, "/api/followup", {**FOLLOWUP, "promise_date": FUTURE, "promise_amount": "50,000", "actor": "priya"})
    assert status == 200 and body["followup_id"] == 1 and body["promise_id"] == 1
    d = call(server, "/api/overview")[2]
    assert [w["customer"] for w in d["waiting"]] == ["Acme Traders"]   # promised, so no longer chased
    assert d["promises"][0]["amount"] == "50000.00" and d["followups"][0]["outcome"] == "Will pay Friday"
    with Store(server.db_path) as s:
        entry = [e for e in s.audit_entries() if e.action == "followup.logged"][0]
        assert entry.actor == "priya" and entry.reason == "spoke to accounts"


def test_default_actor_is_dashboard(server):
    post(server, "/api/followup", FOLLOWUP)
    with Store(server.db_path) as s:
        assert s.audit_entries()[-1].actor == "dashboard"


def test_followup_without_promise(server):
    status, _, body = post(server, "/api/followup", FOLLOWUP)
    assert status == 200 and body["promise_id"] is None


def test_resolve_promise_then_it_cannot_be_resolved_twice(server):
    post(server, "/api/followup", {**FOLLOWUP, "promise_date": FUTURE})
    assert post(server, "/api/promise/resolve", {"id": 1, "status": "kept", "note": "NEFT"})[0] == 200
    assert call(server, "/api/overview")[2]["promises"] == []
    status, _, body = post(server, "/api/promise/resolve", {"id": 1, "status": "broken"})
    assert status == 400 and "already kept" in body["error"]


def test_take_snapshot(server):
    status, _, body = post(server, "/api/snapshot", {"as_of": "2026-10-15"})
    assert status == 200 and body["as_of"] == "2026-10-15" and body["overdue"] == "120000.00"
    assert len(call(server, "/api/overview?as_of=2026-10-15")[2]["trend"]) == 1


# ---- the attacks the design must stop -----------------------------------------------------


@pytest.mark.parametrize("tok", ["", "wrong", "x" * 43])
def test_missing_or_wrong_token_is_refused(server, tok):
    status, _, body = post(server, "/api/followup", FOLLOWUP, tok=tok)
    assert status == 403 and "token" in body["error"]
    assert call(server, "/api/overview")[2]["followups"] == []


def test_a_token_from_another_run_is_useless(tmp_path):
    (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
    a, b = start(make_db(tmp_path / "a")), start(make_db(tmp_path / "b"))
    try:
        assert token(a) != token(b)
        assert post(b, "/api/followup", FOLLOWUP, tok=token(a))[0] == 403
    finally:
        for s in (a, b):
            s.shutdown(); s.server_close()


@pytest.mark.parametrize("origin", ["http://evil.example", "http://127.0.0.1:1", "null", "https://127.0.0.1"])
def test_cross_origin_posts_are_refused_even_with_the_right_token(server, origin):
    status, _, body = post(server, "/api/followup", FOLLOWUP, extra={"Origin": origin})
    assert status == 403 and "cross-origin" in body["error"]


def test_same_origin_requests_are_accepted(server):
    port = server.server_address[1]
    assert post(server, "/api/followup", FOLLOWUP, extra={"Origin": f"http://127.0.0.1:{port}"})[0] == 200
    assert post(server, "/api/followup", FOLLOWUP, extra={"Origin": f"http://localhost:{port}"}, host=f"localhost:{port}")[0] == 200


def test_foreign_host_is_refused_on_posts_too(server):
    assert post(server, "/api/followup", FOLLOWUP, host="evil.example")[0] == 403


@pytest.mark.parametrize("ctype", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data", ""])
def test_only_json_bodies_are_accepted(server, ctype):
    # A cross-site <form> can only send these simple types; they must never reach the handler.
    headers = {web.TOKEN_HEADER: token(server)}
    if ctype:
        headers["Content-Type"] = ctype
    assert call(server, "/api/followup", "POST", FOLLOWUP, headers)[0] == 415


def test_preflight_is_not_granted(server):
    status, headers, _ = call(server, "/api/followup", "OPTIONS", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
    assert status == 405 and not any(k.startswith("access-control") for k in headers)


def test_body_size_limit(server):
    big = b'{"customer": "' + b"a" * (web.MAX_BODY + 10) + b'"}'
    try:
        status, _, _ = call(server, "/api/followup", "POST", headers={"Content-Type": "application/json", web.TOKEN_HEADER: token(server)}, raw=big)
        assert status == 413
    except (ConnectionResetError, BrokenPipeError):
        pass  # the server refused and closed before reading the body: also acceptable


def test_missing_content_length_is_refused(server):
    status, _, _ = call(server, "/api/followup", "POST", headers={"Content-Type": "application/json", web.TOKEN_HEADER: token(server)}, raw=None)
    assert status == 411


@pytest.mark.parametrize("raw", [b"not json", b"[]", b'"text"', b"\xff\xfe"])
def test_malformed_bodies_are_a_clean_400(server, raw):
    status, _, body = call(server, "/api/followup", "POST", headers={"Content-Type": "application/json", web.TOKEN_HEADER: token(server)}, raw=raw)
    assert status == 400 and "error" in body


@pytest.mark.parametrize(
    "patch, fragment",
    [
        ({"customer": "Nobody Ltd"}, "unknown customer"),
        ({"channel": "pigeon"}, "channel must be one of"),
        ({"outcome": "  "}, "outcome is required"),
        ({"customer": ""}, "customer is required"),
        ({"promise_date": "2020-01-01"}, "in the past"),
        ({"promise_date": "garbage"}, "unrecognised date"),
        ({"promise_date": FUTURE, "promise_amount": "abc"}, "not a valid amount"),
        ({"promise_date": FUTURE, "promise_amount": "0"}, "must be positive"),
        ({"note": "x" * 2001}, "note is too long"),
        ({"outcome": 12}, "outcome must be text"),
    ],
)
def test_invalid_followups_get_a_clear_400_and_write_nothing(server, patch, fragment):
    status, _, body = post(server, "/api/followup", {**FOLLOWUP, **patch})
    assert status == 400 and fragment in body["error"]
    d = call(server, "/api/overview")[2]
    assert d["followups"] == [] and d["promises"] == []


@pytest.mark.parametrize("body, fragment", [({"status": "kept"}, "missing field"), ({"id": "1", "status": "kept"}, "whole number"),
                                            ({"id": True, "status": "kept"}, "whole number"), ({"id": 99, "status": "kept"}, "no promise"),
                                            ({"id": 1, "status": "paid"}, "status must be")])
def test_invalid_resolutions(server, body, fragment):
    status, _, out = post(server, "/api/promise/resolve", body)
    assert status == 400 and fragment in out["error"]


def test_read_only_mode_blocks_every_write(tmp_path):
    srv = start(make_db(tmp_path), read_only=True)
    try:
        assert call(srv, "/api/session")[2]["writable"] is False
        for path, body in (("/api/followup", FOLLOWUP), ("/api/promise/resolve", {"id": 1, "status": "kept"}), ("/api/snapshot", {})):
            assert post(srv, path, body)[0] == 405
        with Store(srv.db_path) as s:
            assert s.followups() == [] and s.snapshots() == []
    finally:
        srv.shutdown(); srv.server_close()


def test_posting_to_other_paths_stays_method_not_allowed(server):
    assert post(server, "/api/overview", {})[0] == 405
    assert post(server, "/anything", {})[0] == 405
