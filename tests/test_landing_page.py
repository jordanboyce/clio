"""The landing page (docs/index.html) is one file that names others.

Its screenshots are the README's, which scripts/screenshots.py rewrites and
could rename, so every file it names is checked here. So is what it loads
from elsewhere, which is nothing: the fonts are beside it, under landing/.
"""

import re

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

DOCS = Path(__file__).resolve().parent.parent / "docs"
PAGE = DOCS / "index.html"


class _Page(HTMLParser):
    def __init__(self):
        super().__init__()
        # Addresses the browser fetches to draw the page. A link a visitor
        # follows is not one of them.
        self.loads = []
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
    text = PAGE.read_text(encoding="utf-8")
    parser = _Page()
    parser.feed(text)
    # What the stylesheet fetches: the fonts.
    parser.loads.extend(m.strip("'\"") for m in re.findall(r"url\(([^)]+)\)", text))
    return parser


def test_every_file_the_page_names_exists():
    local = [u for u in _page().loads if not urlparse(u).netloc]
    assert local, "the page shows no screenshots"
    missing = [u for u in local if not (DOCS / u).is_file()]
    assert not missing, f"docs/index.html names files that are not in docs/: {missing}"


def test_the_page_loads_nothing_from_another_site():
    page = _page()
    assert any(u.endswith(".woff2") for u in page.loads), "the page names no fonts"
    elsewhere = sorted(u for u in page.loads if urlparse(u).netloc or u.startswith("//"))
    assert not elsewhere, f"docs/index.html loads from another site: {elsewhere}"


def test_every_tab_and_marker_points_at_something():
    page = _page()
    dangling = sorted(set(page.references) - page.ids)
    assert not dangling, f"docs/index.html points at ids that do not exist: {dangling}"
