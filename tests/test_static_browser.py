"""The static-search components in a real browser (tests/browser_harness.py):
an exported folder served by a plain static server, every other host blocked,
and a page with a Content-Security-Policy that allows no inline script."""

import shutil
from pathlib import Path

import pytest

from services import static_export as se
from tests.browser_harness import static_server
from tests.test_collection_bundle import env  # noqa: F401
from tests.test_static_export import _public, kit, site  # noqa: F401

PAGES = Path(__file__).resolve().parent / "browser"


def serve_site(cid, out: Path, **options):
    """Export a whole site into ``out`` and add the test pages beside it."""
    manifest, _ = se.write_export(cid, out, confirm_public=True, site=True, **options)
    for page in PAGES.iterdir():
        shutil.copyfile(page, out / page.name)
    return manifest


def shadow(selector, inner):
    return f"document.querySelector({selector!r}).shadowRoot.querySelector({inner!r})"


def test_the_search_box_and_a_second_component_share_one_loader(kit, site, browser, tmp_path):
    cid, _ = _public(site)
    out = tmp_path / "site"
    manifest = serve_site(cid, out)
    with static_server(out) as server:
        page = browser.page()
        page.goto(server.url("components.html"))
        count = manifest["passages"]["count"]
        assert page.wait_for(f"{shadow('clio-count', '.status')}.textContent.includes('{count} passages')")
        page.wait_for("!" + shadow("clio-search", "input") + ".disabled")
        page.type_into(shadow("clio-search", "input"), "holiday")
        titles = page.wait_for(f"[...document.querySelector('clio-search').shadowRoot.querySelectorAll('.title')]"
                               ".map(t => t.textContent).join('|') || null")
        assert "holidays" in titles
        # Its styles arrive under a policy with no 'unsafe-inline'.
        assert page.eval(f"getComputedStyle({shadow('clio-search', '.title')}).fontWeight") == "600"
        assert page.eval("window.__violations") == [] and page.eval("window.__errors") == []
        # The manifest was read past the cache; the passages by checksum; nothing else, and no other host.
        assert server.asked_for("manifest.json") and server.asked_for("passages.json?v=")
        assert all(u.startswith(server.url()) for u in page.requested())
        page.close()
