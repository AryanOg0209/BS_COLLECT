"""One interface: the website at /, the dashboard at /app/, links both ways, and a synthetic demo mode."""

import http.client
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from collectos import cli, web
from collectos.importer import import_rows
from collectos.store import Store

HEADER = ["Invoice No", "Customer", "Invoice Date", "Due Date", "Amount", "Paid"]
WEBUI = Path(web.__file__).parent / "webui"


def make_db(tmp_path):
    path = tmp_path / "t.db"
    with Store(path, now=lambda: datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)) as s:
        s.apply_import(import_rows([(1, HEADER), (2, ["INV-1", "Acme Traders", "01-06-2026", "01-07-2026", "1000", "0"])]), actor="t")
    return path


@pytest.fixture
def servers(tmp_path):
    made = []

    def start(demo=False):
        srv = web.make_server(make_db(tmp_path), demo=demo)
        threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.02), daemon=True).start()
        made.append(srv)
        return srv
    yield start
    for s in made:
        s.shutdown(); s.server_close()


def get(srv, path, method="GET"):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    conn.putrequest(method, path, skip_host=True)
    conn.putheader("Host", f"127.0.0.1:{srv.server_address[1]}")
    conn.endheaders()
    r = conn.getresponse(); body = r.read(); headers = {k.lower(): v for k, v in r.getheaders()}; conn.close()
    return r.status, headers, body


# ---- the website at / ------------------------------------------------------------------------------------------------------

def test_home_is_the_website_pointed_at_this_dashboard(servers):
    srv = servers()
    status, h, body = get(srv, "/")
    page = body.decode()
    assert status == 200 and h["content-type"].startswith("text/html") and "Know who to call today" in page
    assert 'href="/app/">Open dashboard</a>' in page and 'href="/app/">Open the dashboard</a>' in page
    assert "<!--app-" not in page and "<!--/app-" not in page
    assert "Become a pilot partner" not in page.split("</header>")[0] and 'href="#product"' in page     # the app swaps the marketing button for the dashboard one
    for label, route, _ in web.APP_LINKS:
        assert f'href="/app/#/{route}"' in page and f"<h3>{label}</h3>" in page
    assert "demo-note" not in page and "default-src" in h["content-security-policy"]


def test_the_site_file_on_disk_keeps_its_standalone_buttons(servers):
    raw = (Path(web.__file__).parent / "site" / "index.html").read_text()
    assert "<!--app-nav-->" in raw and 'href="#pilot"' in raw and "/app/" not in raw     # hosted on its own, it is a pure marketing page


def test_demo_mode_says_so_on_the_home_page_only_when_demo(servers):
    assert "synthetic demo dataset" in get(servers(demo=True), "/")[2].decode()
    assert "synthetic demo dataset" not in get(servers(demo=False), "/")[2].decode()


def test_home_head_and_post(servers):
    srv = servers()
    assert get(srv, "/", "HEAD")[0] == 200
    assert get(srv, "/", "POST")[0] == 405


# ---- the dashboard at /app/ ------------------------------------------------------------------------------------------------

def test_dashboard_lives_at_app_and_links_home(servers):
    srv = servers()
    for path in ("/app", "/app/"):
        status, h, body = get(srv, path)
        assert status == 200 and b'src="/static/app.js"' in body and b'href="/">' in body and h["content-type"].startswith("text/html")
    assert get(srv, "/api/session")[0] == 200


def test_every_site_link_targets_a_real_dashboard_screen():
    nav_ids = set(re.search(r"const NAV_ORDER = \[(.*?)\]", (WEBUI / "app.js").read_text()).group(1).replace('"', "").replace(" ", "").split(","))
    assert {route for _, route, _ in web.APP_LINKS} == nav_ids


# ---- site assets: a strict allow-list --------------------------------------------------------------------------------------

@pytest.mark.parametrize("path, ctype", [("/site.css", "text/css"), ("/site.js", "text/javascript"), ("/img/dashboard.jpg", "image/jpeg"), ("/img/logo.svg", "image/svg+xml")])
def test_site_assets_are_served_with_the_right_type(servers, path, ctype):
    status, h, body = get(servers(), path)
    assert status == 200 and h["content-type"].startswith(ctype) and h["x-content-type-options"] == "nosniff" and len(body) > 100


@pytest.mark.parametrize("path", ["/img/../web.py", "/img/%2e%2e/web.py", "/img/nope.jpg", "/img/a/b.jpg", "/site.css/../web.py", "/img/x.py", "/img/", "/img",
                                   "/site.py", "/img/..%2fstore.py", "/img/dashboard.jpg%00.py", "/static/../site/index.html"])
def test_site_asset_paths_cannot_escape(servers, path):
    status, _, body = get(servers(), path)
    assert status == 404 and b"def " not in body and b"import " not in body


# ---- demo mode from the CLI ------------------------------------------------------------------------------------------------

def test_ui_demo_serves_a_synthetic_read_only_dataset_and_never_opens_your_database(monkeypatch, tmp_path):
    seen = {}

    def fake_serve(db, port=8765, open_browser=True, read_only=False, demo=False):
        with Store(db) as s:
            seen.update(read_only=read_only, demo=demo, invoices=len(s.invoices()), name=Path(db).name, existed=Path(db).is_file())
    monkeypatch.setattr(web, "serve", fake_serve)
    assert cli.main(["ui", "--demo", "--no-open", "--db", str(tmp_path / "not-your-real.db")]) == 0
    assert seen["read_only"] is True and seen["demo"] is True and seen["invoices"] > 20 and seen["existed"] and seen["name"] == "demo.db"
    assert not (tmp_path / "not-your-real.db").exists()
