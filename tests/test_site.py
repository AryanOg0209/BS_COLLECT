"""The public website in site/: self-contained, accessible, and honest. It is a public page, so it must never carry pricing,
prospect details, testimonials or recovery promises."""

import re
from html.parser import HTMLParser
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"
HTML = (SITE / "index.html").read_text(encoding="utf-8")
CSS = (SITE / "site.css").read_text(encoding="utf-8")
JS = (SITE / "site.js").read_text(encoding="utf-8")
ALLOWED_URLS = {"http://www.w3.org/2000/svg"}


class Collect(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.ids, self.hrefs, self.imgs, self.inline_scripts, self.inline_styles, self.headings = [], set(), [], [], 0, 0, []
        self._in = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append(tag)
        if "id" in a:
            self.ids.add(a["id"])
        if tag == "a" and "href" in a:
            self.hrefs.append(a["href"])
        if tag == "img":
            self.imgs.append(a)
        if tag == "script" and "src" not in a:
            self.inline_scripts += 1
        if "style" in a or tag == "style":
            self.inline_styles += 1
        if tag in ("h1", "h2", "h3"):
            self._in = tag

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3"):
            self._in = None


page = Collect()
page.feed(HTML)


def test_nothing_is_loaded_from_other_origins():
    for name, text in (("index.html", HTML), ("site.css", CSS), ("site.js", JS)):
        urls = [u for u in re.findall(r"https?://[^\s\"')`<>]+", text) if u not in ALLOWED_URLS]
        assert not urls, f"{name} references {urls}"
    assert "default-src 'none'" in HTML and "script-src 'self'" in HTML
    assert "@import" not in CSS and "url(" not in CSS.replace("url(\"data", "")  # no remote or data images in CSS (the CSP blocks them)
    for banned in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "innerHTML", "eval(", "document.write"):
        assert banned not in JS


def test_csp_friendly_no_inline_script_or_style():
    assert page.inline_scripts == 0 and page.inline_styles == 0


def test_structure_and_accessibility_basics():
    assert page.tags.count("h1") == 1 and 'lang="en"' in HTML and 'name="viewport"' in HTML
    assert "main" in page.tags and "nav" in page.tags and "footer" in page.tags and "header" in page.tags
    assert page.imgs and all(i.get("width") and i.get("height") and "alt" in i for i in page.imgs)
    assert all(i["alt"] for i in page.imgs if i["src"].startswith("img/") and not i["src"].endswith("logo.svg"))
    for href in page.hrefs:
        if href.startswith("#") and len(href) > 1:
            assert href[1:] in page.ids, f"dead anchor {href}"
    assert 'class="skip"' in HTML and 'role="tablist"' in HTML and "prefers-reduced-motion" in CSS and "prefers-reduced-motion" in JS
    for t in re.findall(r'role="tab"[^>]*aria-controls="([^"]+)"', HTML):
        assert t in page.ids


def test_every_referenced_file_exists():
    for ref in re.findall(r'(?:src|href)="((?:img/)[^"]+|site\.(?:css|js))"', HTML):
        assert (SITE / ref).is_file(), ref


def test_screenshots_are_light_enough_to_load_quickly():
    for f in (SITE / "img").glob("*.jpg"):
        assert f.stat().st_size < 300_000, f"{f.name} is large"


def test_public_page_stays_honest_and_free_of_private_sales_material():
    text = re.sub(r"<[^>]+>", " ", HTML).lower()
    for banned in ("per month", "/month", "per year", "pricing", "price list", "₹ per", "testimonial", "case study", "guaranteed recovery", "recover 100", "% faster", "trusted by"):
        assert banned not in text, banned
    assert "do not promise" in text and "not yet validated" in text and "synthetic" in text
    assert "no promises about recovery" in text
    assert "gmail" not in HTML.lower() and "@collectos.example" in HTML       # a placeholder, never a personal address
    # every "Demo" customer shown in the hero is labelled synthetic
    assert all("(Demo)" in n for n in re.findall(r"<span>([^<]+\(.*?\))</span>", HTML))
