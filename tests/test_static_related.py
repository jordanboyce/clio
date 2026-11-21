"""C1: related passages from other sources, and <clio-related>."""

import json
import tracemalloc

import numpy as np
import pytest

from services import static_export as se
from services.static_export import related
from tests.browser_harness import static_server
from tests.test_collection_bundle import env  # noqa: F401
from tests.test_static_export import _public, kit, site  # noqa: F401
from tests.test_static_reader import handbook  # noqa: F401


def _unit(rows, d=16, seed=0):
    v = np.random.default_rng(seed).standard_normal((rows, d)).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


@pytest.mark.parametrize("block", [1, 7, 64, 1000])
def test_neighbours_match_a_brute_force_search_and_skip_the_same_source(block):
    v = _unit(300)
    groups = np.arange(300) // 13
    idx, scores = related.neighbours(v, groups, k=5, block=block)
    full = v @ v.T
    full[groups[:, None] == groups[None, :]] = -np.inf
    expected = np.argsort(-full, axis=1, kind="stable")[:, :5]
    assert np.array_equal(np.sort(idx, axis=1), np.sort(expected, axis=1))
    assert (groups[idx] != groups[:, None]).all()
    assert np.allclose(scores, np.take_along_axis(full, idx, axis=1), atol=1e-5)
    assert (np.diff(scores, axis=1) <= 1e-6).all()


def test_a_collection_of_one_source_and_a_tiny_one_have_no_neighbours():
    idx, scores = related.neighbours(_unit(4), np.zeros(4, dtype=np.int64), k=3)
    assert (idx == -1).all()
    idx, _ = related.neighbours(_unit(3), np.array([0, 1, 2]), k=8)
    assert idx.shape == (3, 3) and (np.sort(idx, axis=1)[:, 0] == -1).all()


def test_scoring_in_blocks_needs_less_memory_than_the_vectors_themselves():
    """The install holds the vectors to search them (n x 384 float32). The
    neighbour search adds less than that, whatever n, because it holds one
    block of scores at a time and never the n x n matrix. (At 50,000
    passages: 66 MB beside 77 MB of vectors, about 75 s on a 2018 laptop.)"""
    n, d = 8000, 384
    v = _unit(n, d)
    groups = np.arange(n) // 40
    tracemalloc.start()
    related.neighbours(v, groups)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < v.nbytes, (peak, v.nbytes)
    assert peak < n * n * 4 / 20  # nowhere near the full matrix


def test_each_source_lists_the_best_from_other_sources_two_at_most_from_each():
    passages = [{"id": f"p{i}", "document_id": f"d{i // 4}", "page": 1, "text": f"text {i} " * 60}
                for i in range(16)]
    v = _unit(16)
    idx, scores = related.neighbours(v, np.arange(16) // 4, k=8)
    out = related.per_source(passages, idx, scores)
    assert set(out) == {"d0", "d1", "d2", "d3"}
    for doc_id, items in out.items():
        assert items and all(p["document_id"] != doc_id for p in items)
        per = {}
        for p in items:
            per[p["document_id"]] = per.get(p["document_id"], 0) + 1
        assert max(per.values()) <= related.FROM_ONE_SOURCE and len(items) <= related.PER_SOURCE
        assert [p["score"] for p in items] == sorted((p["score"] for p in items), reverse=True)
        assert all(len(p["text"]) <= related.EXCERPT + 1 for p in items)


def test_the_export_writes_one_small_file_per_source_and_puts_the_box_on_reader_pages(kit, handbook, tmp_path):
    cid, ids = handbook
    out = tmp_path / "site"
    manifest, _ = se.write_export(cid, out, confirm_public=True, site=True, include="reader,related")
    entry = manifest["additions"]["related"]
    assert set(entry["sources"]) == set(ids)
    for doc_id, name in entry["sources"].items():
        data = json.loads((out / "search" / name).read_text(encoding="utf-8"))
        assert data["source"] == doc_id and data["related"]
        assert all(p["document_id"] != doc_id and p["document_id"] in ids for p in data["related"])
    page = (out / "search/read/travel-policy.html").read_text(encoding="utf-8")
    assert f'<clio-related src="../" source="{ids[2]}"' in page and 'src="../../clio-related.js"' in page
    assert "clio-related" not in (out / "search/read/index.html").read_text(encoding="utf-8")
    # The data-only export cannot carry it: the component ships with the site.
    with pytest.raises(se.StaticExportError, match="needs a whole site"):
        se.write_export(cid, tmp_path / "data", confirm_public=True, include="related")


def test_a_keyword_export_offers_no_related_passages(env, tmp_path):  # noqa: F811
    cid, _ = _public(env)  # fake-embed has no browser build here: searched by keyword
    offer = next(a for a in se.preview(cid)["additions"] if a["id"] == "related")
    assert offer["available"] is False and "searched by meaning" in offer["reason"]


def test_a_reader_page_shows_related_passages_without_the_model_or_the_vectors(kit, handbook, browser, tmp_path):
    cid, ids = handbook
    out = tmp_path / "site"
    se.write_export(cid, out, confirm_public=True, site=True, include="reader,related")
    with static_server(out) as server:
        page = browser.page()
        page.goto(server.url("search/read/travel-policy.html"))
        box = "document.querySelector('clio-related')"
        titles = page.wait_for(f"(() => {{ const t = [...{box}.shadowRoot.querySelectorAll('.title')];"
                               " return t.length ? t.map(a => a.textContent + '|' + (a.href || '')) : null })()")
        names = {t.split("|")[0] for t in titles}
        assert names and "travel policy" not in names and names <= {"holidays", "expenses", "visitor notes"}
        # Each one links to its passage on its own reader page.
        assert all("/search/read/" in t and "#p-" in t for t in titles)
        assert page.eval(f"{box}.shadowRoot.querySelector('.status').textContent") == ""
        page.drain()
        asked = server.requests
        assert not [r for r in asked if "vectors.bin" in r or "/models/" in r or "/vendor/" in r or "passages" in r]
        assert [r for r in asked if r.startswith("/search/related/travel-policy.json")]
        # The search box on the page loads the index only once it is used.
        page.type_into("document.querySelector('clio-search').shadowRoot.querySelector('input')", "holiday")
        page.wait_for("document.querySelector('clio-search').shadowRoot.querySelectorAll('li').length > 0")
        assert [r for r in server.requests if "passages.json" in r]
        page.close()


def test_a_page_with_no_related_file_takes_no_room(kit, handbook, browser, tmp_path):
    cid, _ = handbook
    out = tmp_path / "site"
    se.write_export(cid, out, confirm_public=True, site=True, include="reader")
    (out / "elsewhere.html").write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>x</title>'
        '<script type="module" src="clio-related.js"></script></head>'
        '<body><clio-related src="search/"></clio-related></body></html>', encoding="utf-8")
    with static_server(out) as server:
        page = browser.page()
        page.goto(server.url("elsewhere.html"))
        assert page.wait_for("document.querySelector('clio-related').hidden === true")
        assert "no related" in page.eval(
            "document.querySelector('clio-related').shadowRoot.querySelector('.status').textContent").lower()
        page.close()
