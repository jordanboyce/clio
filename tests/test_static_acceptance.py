"""The work order's acceptance run (docs/prompts, "more to publish"): the
sample collection exported as a whole site with everything chosen, served by
a plain static server with every other host blocked, with Clio's real model
and the browser runtime the image carries. Needs CLIO_TEST_STATIC_RUNTIME
(tests/static_real.py); skipped without it. Each step is one block below,
numbered as in the work order."""

import csv
import io
import json
import re
import shutil
from pathlib import Path

from services import static_export as se
from services.static_export import answers
from tests.browser_harness import static_server
from tests.static_real import add_source, passages_of, real_env, real_model_cache, sample_collection  # noqa: F401

V1_7 = Path(__file__).resolve().parent / "fixtures" / "static_v1_7"
BOX = "document.querySelector('clio-search')"


def _search(page, words, *, semantic=True):
    page.wait_for(f"{BOX} && {BOX}.shadowRoot && !{BOX}.shadowRoot.querySelector('input').disabled")
    page.type_into(f"{BOX}.shadowRoot.querySelector('input')", "x")
    if semantic:
        page.wait_for(f"{BOX}.search && {BOX}.search.semantic", timeout=120)
    page.type_into(f"{BOX}.shadowRoot.querySelector('input')", words)
    page.wait_for(f"{BOX}.shadowRoot.querySelectorAll('li').length > 0", timeout=60)
    page.eval("new Promise(r => setTimeout(r, 500))")
    return page.eval(f"[...{BOX}.shadowRoot.querySelectorAll('li')].map(li => ({{"
                     "href: li.querySelector('a.title')?.href || null, text: li.textContent}))")


def _all_text(folder: Path) -> bytes:
    return b"".join(p.read_bytes() for p in folder.rglob("*") if p.is_file()
                    and "models" not in p.parts and "vendor" not in p.parts)


def test_acceptance(real_env, browser, tmp_path):  # noqa: C901 - one run, step by step
    from scripts import export_static

    cid, ids = sample_collection()
    se.save_addresses(cid, site_url="https://handbook.example.org/")
    policy = ids["01-equipment-policy.md"]
    cite = next(p["id"] for p in passages_of(cid, policy) if "five working days" in p["text"])
    row = answers.add_answer(cid, "When must shared equipment be returned?",
                             "Within five working days of checkout [Source 1].", [cite], actor="owner")
    answers.approve_answer(cid, row["id"], actor="owner")
    out = tmp_path / "site"
    args = [cid, "--out", str(out), "--site", "--confirm-public", "--include", "all"]
    assert export_static.main(args) == 0
    manifest = json.loads((out / "search/manifest.json").read_text(encoding="utf-8"))
    chosen = set(manifest["additions"])
    assert {"reader", "related", "answers", "tables", "llms", "changes", "offline"} <= chosen
    report = {"additions": sorted(chosen)}

    with static_server(out) as server:
        port = server.httpd.server_address[1]
        page = browser.page()

        # 1. The contents page lists four sources. Each opens as a page.
        page.goto(server.url("search/read/index.html"))
        links = page.eval("[...document.querySelectorAll('ol.sources a')].map(a => a.href)")
        assert len(links) == 4
        for href in links:
            page.goto(href)
            assert page.wait_for("location.href === " + json.dumps(href) + " && document.querySelector('main') !== null"
                                 " && (document.querySelectorAll('.passage').length > 0 || document.querySelector('clio-table') !== null)"), href
        report["1"] = f"{len(links)} sources, each page opens"
        register_passages = next(d["passages"] for d in manifest["documents"] if d["name"] == "equipment-register.csv")
        report["1"] += f" (the register's page: {register_passages} passages and its table)"

        # 2. Search from a reader page: every result is a link, landing on the passage.
        page.goto(server.url("search/read/01-equipment-policy.html"))
        hits = _search(page, "how long can I keep borrowed equipment?")
        assert hits and all(h["href"] for h in hits)
        page.goto(hits[0]["href"])
        anchor = hits[0]["href"].split("#", 1)[1]
        assert page.eval(f"document.querySelector(':target')?.id === {anchor!r}")
        report["2"] = f"{len(hits)} results, all links; the first lands on #{anchor}"

        # 3. Related passages from other sources, with no model and no vectors downloaded.
        server.requests.clear()
        page.goto(server.url("search/read/02-equipment-amendment.html"))
        rel = "document.querySelector('clio-related').shadowRoot"
        titles = page.wait_for(f"(() => {{ const t = [...{rel}.querySelectorAll('.title')].map(a => a.textContent);"
                               " return t.length ? t : null })()")
        assert titles and all("amendment" not in t.lower() for t in titles)
        heavy = [r for r in server.requests if "vectors.bin" in r or "/models/" in r or "/vendor/" in r]
        assert not heavy, heavy
        report["3"] = f"related: {titles}; no model, vectors or runtime fetched"

        # 4. A paraphrase shows the published answer; an unrelated query shows none.
        page.goto(server.url("index.html"))
        _search(page, "What is the deadline for returning checked-out gear?")
        shown = page.eval(f"(() => {{ const a = {BOX}.shadowRoot.querySelector('.answer'); return a.hidden ? null : a.textContent }})()")
        assert shown and "five working days" in shown
        assert page.eval(f"{BOX}.shadowRoot.querySelector('.answer a').href").startswith(server.url())
        _search(page, "What is the capital of Australia?")
        assert page.eval(f"{BOX}.shadowRoot.querySelector('.answer').hidden") is True
        report["4"] = "paraphrase answered with a working citation; unrelated query, no answer"

        # 5. The checkout register sorts by date and filters by name; its CSV runs no formula.
        page.goto(server.url("search/read/equipment-register.html"))
        t = "document.querySelector('clio-table').shadowRoot"
        page.wait_for(f"{t}.querySelectorAll('tbody tr').length > 0")
        col = page.eval(f"[...{t}.querySelectorAll('th button')].findIndex(b => b.textContent.startsWith('checkout_date'))")
        page.eval(f"{t}.querySelectorAll('th button')[{col}].click()")
        dates = page.eval(f"[...{t}.querySelectorAll('tbody tr')].map(r => r.children[{col}].textContent)")
        assert dates == sorted(dates)
        page.type_into(f"{t}.querySelector('input')", "Laptop")
        items = page.eval(f"[...{t}.querySelectorAll('tbody tr')].map(r => r.children[1].textContent)")
        assert items and all("Laptop" in i for i in items)
        csv_file = next(out.rglob("equipment-register.csv"))
        rows = list(csv.reader(io.StringIO(csv_file.read_text(encoding="utf-8-sig"))))
        assert rows and not [c for r in rows for c in r if c and c[0] in "=+@\t\r"]
        report["5"] = f"sorted {len(dates)} rows by date, filtered to {len(items)}; CSV has no formula cell"

        # 11. llms.txt is at the root and lists the four sources.
        llms = (out / "llms.txt").read_text(encoding="utf-8")
        assert len(re.findall(r"^- \[", llms, re.M)) == 4
        report["11"] = "llms.txt lists 4 sources"

        # 10. A site that still has last release's clio-search.js searches the new export as before.
        old = tmp_path / "old-site"
        old.mkdir()
        for f in V1_7.iterdir():
            shutil.copyfile(f, old / f.name)
        shutil.copytree(out / "search", old / "search")
        for d in ("vendor", "models"):
            shutil.copytree(out / d, old / d)
        (old / "index.html").write_text(
            '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>old</title>'
            '<script type="module" src="clio-search.js"></script></head><body>'
            '<clio-search src="search/"></clio-search></body></html>', encoding="utf-8")
        with static_server(old) as old_server:
            page.goto(old_server.url("index.html"))
            old_hits = _search(page, "how long can I keep borrowed equipment?")
            assert old_hits and page.eval(f"{BOX}.search.semantic") is True
        report["10"] = f"v1.7 clio-search.js: {len(old_hits)} results, by meaning"

        # 8. Load the site, go offline, reload, search.
        page.goto(server.url("index.html"))
        page.wait_for("navigator.serviceWorker.controller !== null", timeout=30)
        page.wait_for("(async () => (await (await caches.open('clio-files')).keys()).length > 3)()", timeout=60)
    page.goto(f"http://127.0.0.1:{port}/index.html")
    offline_hits = _search(page, "equipment return", semantic=False)
    assert offline_hits
    report["8"] = f"server stopped: the site loads and finds {len(offline_hits)} results"

    # 9. Change one source, export over the old folder, read the feed: one entry.
    note = ids["03-equipment-desk-note.md"]
    from services.indexer_manager import indexer_manager

    indexer = indexer_manager.get_indexer(cid)
    indexer.delete_document(note)
    indexer.save_index()
    add_source(cid, "03-equipment-desk-note.md", "# Desk note\n\nReturn equipment within ten working days. Revised.\n")
    assert export_static.main(args) == 0
    feed = (out / "search/changes.xml").read_text(encoding="utf-8")
    assert feed.count("<entry>") == 1 and "Changed: 03 equipment desk note" in feed
    report["9"] = "one feed entry: Changed: 03 equipment desk note"

    # 6. Mark one source as held and export again: in no file, and no count gives it away.
    from services.collection_service import collection_service
    import sqlite3

    register = ids["equipment-register.csv"]
    with sqlite3.connect(collection_service.get_indexes_path(cid) / "metadata.db") as conn:
        conn.execute("UPDATE documents SET policy_status='quarantined' WHERE document_id=?", (register,))
    assert export_static.main(args) == 0
    published = _all_text(out / "search") + (out / "llms.txt").read_bytes() + (out / "llms-full.txt").read_bytes()
    for secret in (register.encode(), b"equipment-register", b"C-1001", b"Survey tablet 04"):
        assert secret not in published, secret
    m = json.loads((out / "search/manifest.json").read_text(encoding="utf-8"))
    assert len(m["documents"]) == 3 and "held" not in json.dumps(m)
    report["6"] = "held register: its id, name and rows appear in no file; the manifest lists 3 sources"

    # 7. A source holding <img src=x onerror=alert(1)>: nothing runs on any page.
    add_source(cid, "visitor-notes.md", "Notes. <img src=x onerror=alert(1)> <script>alert(2)</script> end.\n")
    assert export_static.main(args) == 0
    with static_server(out) as server:
        page = browser.page()
        pages = [p.relative_to(out).as_posix() for p in (out / "search/read").glob("*.html")] + ["index.html"]
        for name in pages:
            page.goto(server.url(name))
            page.eval("new Promise(r => setTimeout(r, 300))")
            assert page.eval("document.querySelectorAll('img[src=\"x\"], script:not([src])').length") == 0, name
        _search(page, "visitor notes img onerror", semantic=False)
        page.eval("new Promise(r => setTimeout(r, 500))")
        assert page.dialogs() == []
        assert page.eval(f"{BOX}.shadowRoot.querySelectorAll('img').length") == 0
        page.close()
    report["7"] = f"{len(pages)} pages and the search box: no element made from the text, no dialog"
    print("ACCEPTANCE", json.dumps(report, indent=1))
