"""C4: a saved report as one HTML file that opens with the network off."""

import re
from html.parser import HTMLParser

import pytest

from services import report_file
from services.app_database import app_db
from services.artifact_store import ArtifactStore
from services.collection_service import collection_service
from services.indexer_manager import indexer_manager
from tests.test_collection_bundle import _source_collection, env  # noqa: F401
from tests.test_static_reader import _Checker


def _report(cid, ids, content, **extra):
    indexer = indexer_manager.get_indexer(cid)
    docs = {d["document_id"]: d for d in indexer.list_documents()}
    store = ArtifactStore(indexer.vector_store.metadata_db_path, collection_service.get_artifacts_path(cid))
    sources = [{"document_id": ids[0], "filename": "holidays.txt", "page_number": 1,
                "text_snippet": "Staff accrue 25 days of holiday a year. <script>alert(1)</script>"},
               {"document_id": ids[1], "filename": "expenses.txt", "page_number": 1,
                "text_snippet": "Expenses are reimbursed within thirty days."}]
    return store.create(
        "rep000000001", artifact_type="faq", title="Staff FAQ", status="completed", content=content,
        sources=sources, created_at="2026-11-20T10:00:00+00:00", finished_at="2026-11-20T10:01:00+00:00",
        source_documents=[{"document_id": i, "filename": docs[i]["filename"], "content_hash": docs[i]["content_hash"]}
                          for i in ids], model="test-model", **extra)


CONTENT = """# Staff questions

**How much holiday?** Staff accrue 25 days a year [Source 1].

- Expenses are paid within thirty days [Source 2].
- Both rules apply [Source 1, Source 2]. See also [holidays.txt p.1] and [Source 9].

<img src=x onerror=alert(1)> and <script>alert('report')</script>
"""


@pytest.fixture()
def client(env, monkeypatch):  # noqa: F811
    from fastapi.testclient import TestClient

    from api import deps
    from main import app

    monkeypatch.setattr(deps, "_initialized", True)
    return TestClient(app)


def _get(client, cid):
    return client.get(f"/api/artifacts/rep000000001/page?collection_id={cid}")


def test_the_file_holds_the_text_each_cited_passage_and_the_sources_with_hashes(env, client):
    cid, ids = _source_collection(env)
    _report(cid, ids, CONTENT)
    r = _get(client, cid)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert 'filename="staff-faq.html"' in r.headers["content-disposition"]
    page = r.text
    # Every citation links to a passage that is in the file.
    targets = re.findall(r'href="#(passage-\d+)"', page)
    assert targets and set(targets) == {"passage-1", "passage-2"}
    assert all(f'id="{t}"' in page for t in targets)
    assert "[9?]" in page  # a citation of a source the report does not have says so
    assert "Staff accrue 25 days of holiday a year." in page and "reimbursed within thirty days" in page
    # Nothing in the report or a passage became markup, and nothing is fetched.
    assert "<script" not in page and "<img" not in page and "onerror=alert(1)&gt;" in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "default-src 'none'" in page and not re.search(r'(src|href)="(https?:)?//', page)
    hashes = [d["content_hash"] for d in indexer_manager.get_indexer(cid).list_documents()]
    assert all(h in page for h in hashes) and page.count("unchanged") == 2
    assert "<h2>Staff questions</h2>" in page and "<strong>How much holiday?</strong>" in page
    from tests.test_static_export import _audit

    assert _audit("report.exported")[0]["detail"]["sources"] == 2


def test_a_source_changed_since_is_marked_and_the_file_still_has_its_passage(env, client):
    cid, ids = _source_collection(env)
    _report(cid, ids, CONTENT)
    import sqlite3

    db = collection_service.get_indexes_path(cid) / "metadata.db"
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE documents SET content_hash='different' WHERE document_id=?", (ids[1],))
    page = _get(client, cid).text
    assert "changed since" in page and "reimbursed within thirty days" in page


def test_a_report_on_a_local_only_collection_carries_its_marking(env, client):
    cid, ids = _source_collection(env)
    app_db.update_collection(cid, classification="cui")
    _report(cid, ids, CONTENT)
    r = _get(client, cid)
    assert r.status_code == 200
    assert '<p class="marking" role="note">Marked CUI. Local only' in r.text
    # One marked source marks the file too.
    app_db.update_collection(cid, classification="")
    indexer_manager.get_indexer(cid).vector_store.metadata_store.set_document_governance(
        ids[0], classification="proprietary")
    assert "Marked Proprietary. Local only" in _get(client, cid).text


def test_a_report_held_from_the_reader_is_not_there(env, client, monkeypatch):
    cid, ids = _source_collection(env, quarantine=True)
    _report(cid, ids, CONTENT)
    from api import artifacts

    monkeypatch.setattr(artifacts, "_can_review", lambda: False)
    assert _get(client, cid).status_code == 404


def test_the_file_is_a_valid_page_with_no_script():
    page = report_file.render({"title": "T", "content": CONTENT, "sources": [], "source_documents": []},
                              {"id": "c", "name": "C"}, {}, {}, sensitivity="public")
    c = _Checker()
    c.feed(page)
    c.close()
    assert not c.problems and not c.stack, (c.problems, c.stack)
    # One file, so its style is inline; no script of any kind, and nothing to fetch.
    assert "script" not in [t for t, _ in c.tags] and c.csp.startswith("default-src 'none'")
    assert not any(k.startswith("on") for _, a in c.tags for k in a)


def test_the_file_opens_from_disk_with_the_network_off(env, client, browser, tmp_path):
    cid, ids = _source_collection(env)
    _report(cid, ids, CONTENT)
    path = tmp_path / "report.html"
    path.write_text(_get(client, cid).text, encoding="utf-8")
    page = browser.page()
    page.set_offline(True)
    try:
        page.goto(path.as_uri())
        assert page.eval("document.title") == "Staff FAQ"
        page.eval("document.querySelector('a.cite').click()")
        assert page.wait_for("location.hash === '#passage-1' && document.querySelector(':target')?.textContent"
                             ".includes('25 days')")
        assert not [u for u in page.requested() if not u.startswith("file:")]
    finally:
        page.set_offline(False)
        page.close()
