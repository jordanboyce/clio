"""The landing page (docs/index.html) is one file that names others.

Its screenshots are the README's, which scripts/screenshots.py rewrites and
could rename, so every file it names is checked here. So is what it loads
from elsewhere: the fonts and nothing more.
"""

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

DOCS = Path(__file__).resolve().parent.parent / "docs"
PAGE = DOCS / "index.html"

# Hosts the page may load from. A link a visitor follows is not a load.
FONT_HOSTS = {"fonts.googleapis.com", "fonts.gstatic.com"}


class _Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.loads = []       # addresses the browser fetches to draw the page
        self.ids = set()
        self.references = []  # ids another element points at

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"])
        for name in ("aria-controls", "aria-labelledby", "data-src", "data-copy"):
            if a.get(name):
                self.references.append(a[name])
        if tag in ("img", "script") and a.get("src"):
            self.loads.append(a["src"])
        if tag == "source" and a.get("srcset"):
            self.loads.extend(part.split()[0] for part in a["srcset"].split(","))
        if tag == "link" and a.get("href"):
            self.loads.append(a["href"])
        if tag == "a" and a.get("href", "").startswith("#"):
            self.references.append(a["href"][1:])


def _page() -> _Page:
    parser = _Page()
    parser.feed(PAGE.read_text(encoding="utf-8"))
    return parser


def test_every_file_the_page_names_exists():
    local = [u for u in _page().loads if not urlparse(u).netloc]
    assert local, "the page shows no screenshots"
    missing = [u for u in local if not (DOCS / u).is_file()]
    assert not missing, f"docs/index.html names files that are not in docs/: {missing}"


def test_the_page_loads_only_its_fonts_from_elsewhere():
    hosts = {urlparse(u).netloc for u in _page().loads} - {""}
    assert hosts <= FONT_HOSTS, f"docs/index.html loads from {sorted(hosts - FONT_HOSTS)}"


def test_every_tab_and_marker_points_at_something():
    page = _page()
    dangling = sorted(set(page.references) - page.ids)
    assert not dangling, f"docs/index.html points at ids that do not exist: {dangling}"
