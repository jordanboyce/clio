"""G2: a whole site that works offline, and shows a new export when back online."""

import json
import struct
import zlib

from services import static_export as se
from services.static_export import offline
from tests.browser_harness import static_server
from tests.test_collection_bundle import env  # noqa: F401
from tests.test_static_export import _public, kit, site  # noqa: F401
from tests.test_static_reader import _add


def test_the_site_carries_a_worker_an_app_manifest_and_icons(kit, site, tmp_path):
    cid, _ = _public(site)
    out = tmp_path / "site"
    manifest, _ = se.write_export(cid, out, confirm_public=True, site=True, include="offline,reader")
    files = {f["file"] for f in manifest["additions"]["offline"]["files"]}
    assert files == {"../sw.js", "../site.webmanifest", "../icon-192.png", "../icon-512.png"}
    app = json.loads((out / "site.webmanifest").read_text(encoding="utf-8"))
    assert app["name"] == "Handbooks" and app["start_url"] == "./index.html" and len(app["icons"]) == 2
    index = (out / "index.html").read_text(encoding="utf-8")
    assert '<link rel="manifest" href="site.webmanifest">' in index and 'src="clio-offline.js"' in index
    assert 'href="search/read/index.html">Read the sources' in index
    assert 'src="../../clio-offline.js"' in (out / "search/read/holidays.html").read_text(encoding="utf-8")
    # The icons are real PNGs.
    raw = (out / "icon-192.png").read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", raw[16:24]) == (192, 192)
    assert zlib.decompress(raw[41:41 + struct.unpack(">I", raw[33:37])[0]])[:4] == b"\x00" + bytes(offline.THEME)
    # Without the option the search page is as it was.
    plain, _ = se.write_export(cid, tmp_path / "plain", confirm_public=True, site=True)
    assert "clio-offline.js\"></script>" not in (tmp_path / "plain" / "index.html").read_text(encoding="utf-8")


def test_after_one_visit_it_searches_with_no_server_and_then_shows_a_new_export(kit, site, browser, tmp_path):
    cid, ids = _public(site)
    out = tmp_path / "site"
    se.write_export(cid, out, confirm_public=True, site=True, include="offline,reader")
    box = "document.querySelector('clio-search')"

    def search(page, words):
        page.wait_for(f"{box}.shadowRoot && !{box}.shadowRoot.querySelector('input').disabled")
        page.eval(f"{box}.setAttribute('model', 'off')")
        page.type_into(f"{box}.shadowRoot.querySelector('input')", words)
        return page.wait_for(f"(() => {{ const r = [...{box}.shadowRoot.querySelectorAll('.snippet')]"
                             ".map(s => s.textContent); return r.length ? r : null })()")

    page = browser.page()
    with static_server(out) as server:
        port = server.httpd.server_address[1]
        page.goto(server.url("index.html"))
        page.wait_for("navigator.serviceWorker.controller !== null", timeout=30)
        # Kept: the manifest, and every file it lists by checksum.
        page.wait_for("(async () => { const k = await (await caches.open('clio-files')).keys();"
                      " return k.some(r => r.url.includes('passages.json?v=')) })()", timeout=30)
        assert any("holiday" in s for s in search(page, "holiday"))
    # The server is gone: the site still opens and searches.
    page.goto(f"http://127.0.0.1:{port}/index.html")
    assert any("holiday" in s for s in search(page, "holiday"))
    assert page.eval("location.href").endswith("/index.html")

    # A new export, then a visit with a connection: the new text is found.
    _add(cid, "parking.txt", "Parking permits are issued by the facilities desk every quarter. " * 10)
    se.write_export(cid, out, confirm_public=True, site=True, include="offline,reader")
    with static_server(out, port=port):
        page.goto(f"http://127.0.0.1:{port}/index.html")
        assert any("Parking permits" in s for s in search(page, "parking permits"))
    page.close()
