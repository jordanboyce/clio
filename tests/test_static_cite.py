"""C3: <clio-cite>, a quotation that shows its source."""

import json

from services import static_export as se
from tests.browser_harness import static_server
from tests.test_collection_bundle import env  # noqa: F401
from tests.test_static_export import _public, kit, site  # noqa: F401
from tests.test_static_reader import handbook  # noqa: F401


def test_three_quotations_read_each_passage_file_once_and_an_unknown_one_says_so(kit, handbook, browser, tmp_path):
    cid, ids = handbook
    se.save_addresses(cid, documents={ids[0]: "https://example.org/holidays.html"})
    out = tmp_path / "site"
    manifest, _ = se.write_export(cid, out, confirm_public=True, site=True, passages_per_file=3)
    files = [f["file"] for f in manifest["passages"]["files"]]
    assert len(files) > 2
    rows = []
    for name in files:
        rows += json.loads((out / "search" / name).read_text(encoding="utf-8"))
    first, middle, last = rows[0], rows[len(rows) // 2], rows[-1]
    hostile = next(r for r in rows if "<script>" in r["text"])
    cites = "".join(f'<clio-cite src="search/" passage="{p["id"]}" words="12"></clio-cite>\n'
                    for p in (first, middle, last, hostile))
    (out / "article.html").write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Article</title>'
        '<script type="module" src="clio-cite.js"></script></head><body>\n' + cites +
        '<clio-cite src="search/" passage="no-such-passage"></clio-cite>\n</body></html>\n', encoding="utf-8")
    with static_server(out) as server:
        page = browser.page()
        page.goto(server.url("article.html"))
        page.wait_for("[...document.querySelectorAll('clio-cite')].every(c => !c.shadowRoot.textContent"
                      ".includes('Loading'))")
        shown = page.eval("[...document.querySelectorAll('clio-cite')].map(c => c.shadowRoot.textContent)")
        assert first["text"].split()[0] in shown[0] and shown[0].count(" ") < 30
        assert "holidays" in shown[0] or "expenses" in shown[0]
        assert shown[-1] == "Passage no-such-passage is not in this export."
        assert "<script>alert" in shown[3]
        assert page.eval("document.querySelectorAll('clio-cite')[3].shadowRoot.querySelector('script')") is None
        links = page.eval("[...document.querySelectorAll('clio-cite')].map(c => c.shadowRoot.querySelector('a')?.href || null)")
        holiday = [s for s, href in zip(shown, links) if href and href.startswith("https://example.org/holidays.html")]
        assert all("holidays" in s for s in holiday)
        for name in files:
            assert len(server.asked_for(f"/search/{name}")) <= 1, (name, server.requests)
        assert len(server.asked_for("manifest.json")) == 1
        assert not server.asked_for("vectors.bin")
        page.close()
