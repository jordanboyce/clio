"""Pasted text as a source: the note the Sources panel's editor sends.

services.pasted_text turns editor markup or plain text into a clean HTML
document; the upload service saves and indexes it as source_type='text';
the endpoint validates and starts the job.
"""

import json
import time

import pytest

import config
from services import pasted_text
from services.pasted_text import PastedTextError, build_document, clean_html, text_to_html


# ── cleaning ──────────────────────────────────────────────────────────


def test_formatting_a_reader_needs_survives_and_nothing_else_does():
    cleaned = clean_html(
        '<div class="x" style="color:red" onclick="steal()"><h2 id="t">Title</h2>'
        '<p>Some <b>bold</b> and <span style="font-weight:bold">styled</span> text.</p>'
        '<script>alert(1)</script><img src="http://t.example/p.gif">'
        '<ul><li>one</li><li>two</li></ul>'
        '<a href="https://example.com/x" target="_blank" onmouseover="x()">link</a> '
        '<a href="javascript:alert(1)">bad</a>'
        '<table><tr><th>a</th><td>b</td></tr></table></div>'
    )
    assert cleaned == (
        '<div><h2>Title</h2><p>Some <b>bold</b> and styled text.</p>'
        '<ul><li>one</li><li>two</li></ul>'
        '<a href="https://example.com/x">link</a> <a>bad</a>'
        '<table><tr><th>a</th><td>b</td></tr></table></div>'
    )


def test_word_processor_paste_is_reduced_to_its_text():
    # A Word/Google Docs paste: a <head> with styles, comments, font spans.
    pasted = ("<html><head><meta charset='utf-8'><style>p{margin:0}</style></head><body>"
              "<!--StartFragment--><p class='MsoNormal'><span style='font-family:Calibri'>Hello"
              "</span> <font face='Arial'>world</font></p><!--EndFragment--></body></html>")
    assert clean_html(pasted) == "<p>Hello world</p>"


def test_plain_text_becomes_paragraphs_with_line_breaks():
    assert text_to_html("First line\nsecond line\n\n\nNext paragraph <tag>\r\n") == (
        "<p>First line<br>second line</p>\n<p>Next paragraph &lt;tag&gt;</p>"
    )


# ── build_document ────────────────────────────────────────────────────


def test_document_is_a_titled_html_file_indexed_like_an_upload():
    doc = build_document("Meeting notes: Q3 / plan", html="<h1>Agenda</h1><p>Budget.</p>")
    assert doc.title == "Meeting notes: Q3 / plan"
    assert doc.filename == "Meeting notes Q3 plan.html"
    body = doc.body.decode("utf-8")
    assert "<title>Meeting notes: Q3 / plan</title>" in body
    assert "<h1>Agenda</h1><p>Budget.</p>" in body
    assert doc.chars == len("Agenda Budget.")


def test_blank_title_comes_from_the_first_words():
    doc = build_document("", text="Employees must file the request two weeks ahead of travel.\n\nMore.")
    assert doc.title == "Employees must file the request two weeks ahead of travel"
    assert doc.filename == "Employees must file the request two weeks ahead of travel.html"
    long = build_document(None, text="word " * 40)
    assert len(long.title) <= 80 and not long.title.endswith(" ")


def test_html_wins_over_text_and_empty_markup_falls_back():
    assert build_document("t", html="<p>rich</p>", text="plain").chars == 4
    assert build_document("t", html="   ", text="plain").chars == 5


@pytest.mark.parametrize("html,text", [("", ""), ("<p> </p><script>x()</script>", None), (None, "\n\n  ")])
def test_nothing_readable_is_refused(html, text):
    with pytest.raises(PastedTextError, match="no text"):
        build_document("t", html=html, text=text)


def test_caps_on_title_and_text():
    with pytest.raises(PastedTextError, match="title"):
        build_document("x" * 201, text="hello")
    with pytest.raises(PastedTextError, match="upload it as a file"):
        build_document("t", text="x" * (pasted_text.MAX_TEXT_CHARS + 1))


def test_title_escaped_in_the_saved_file():
    body = build_document("<b>Bold</b> & co", text="hi").body.decode("utf-8")
    assert "<title>&lt;b&gt;Bold&lt;/b&gt; &amp; co</title>" in body


def test_inline_formatting_does_not_break_the_extracted_sentence(tmp_path):
    """Bold words and links stay on their sentence's line; blocks start new ones."""
    from services.document_extractor import DocumentExtractor

    doc = build_document("EQ-17", html=(
        "<h2>Equipment policy</h2><p>Requests for <b>field equipment</b> must be filed "
        "<i>two weeks</i> ahead.</p><ul><li>Late requests need a <a href=\"https://x.example\">"
        "director's approval</a>.</li><li>Lost items are reported.</li></ul>"
    ))
    path = tmp_path / doc.filename
    path.write_bytes(doc.body)
    text = DocumentExtractor(enable_ocr=False).extract_text(path).page_texts[1]
    lines = [line for line in text.splitlines() if line]
    assert lines == [
        "EQ-17", "Equipment policy",
        "Requests for field equipment must be filed two weeks ahead.",
        "Late requests need a director's approval.",
        "Lost items are reported.",
    ]


# ── the job ───────────────────────────────────────────────────────────


def test_text_job_saves_the_note_and_marks_it_as_text(tmp_path, monkeypatch):
    import services.upload_service as us
    from tests.test_governance import _make_indexer

    indexer = _make_indexer(tmp_path)
    docs_dir = tmp_path / "docs"
    monkeypatch.setattr(us.indexer_manager, "get_indexer", lambda cid: indexer)
    monkeypatch.setattr(us.indexer_manager, "get_documents_path", lambda cid: docs_dir)
    job_updates = []
    monkeypatch.setattr(us.app_db, "update_upload_job", lambda job_id, **kw: job_updates.append(kw))
    added = []
    monkeypatch.setattr(us.collection_service, "add_document", lambda cid, did: added.append(did))

    service = us.UploadService()
    for job_id in (5, 6):
        service._cancel_flags[job_id] = False
    note = build_document("Travel policy", html="<h1>Policy 12</h1><p>File the request two weeks ahead.</p>")
    service._run_text_index(5, note, "c1", uploaded_by="sam@example.com")
    # The same title again with different words: a second file, not an overwrite.
    revised = build_document("Travel policy", html="<h1>Policy 12</h1><p>Late requests need approval.</p>")
    service._run_text_index(6, revised, "c1", uploaded_by="sam@example.com")

    final = [u for u in job_updates if u.get("status") == "completed"]
    assert len(final) == 2, job_updates
    summary = json.loads(final[-1]["result_summary"])
    assert summary["documents_processed"] == 1 and summary["failed_files"] == []

    assert sorted(p.name for p in docs_dir.iterdir()) == ["Travel policy.html", "Travel policy_1.html"]
    docs = indexer.list_documents()
    assert len(docs) == 2 and len(added) == 2
    for doc in docs:
        assert doc["source_type"] == "text"
        assert doc["source_path"] is None
        assert doc["uploaded_by"] == "sam@example.com"
    first = next(d for d in docs if d["filename"] == "Travel policy.html")
    chunks = indexer.vector_store.metadata_store.get_chunks_by_document(first["document_id"])
    assert "two weeks ahead" in " ".join(" ".join(c["text"].split()) for c in chunks)


def test_text_job_counts_against_the_storage_cap(tmp_path, monkeypatch):
    import services.upload_service as us
    from services import storage_quota
    from tests.test_governance import _make_indexer

    indexer = _make_indexer(tmp_path)
    docs_dir = tmp_path / "docs"
    monkeypatch.setattr(config.settings, "collection_storage_limit_bytes", 50)
    monkeypatch.setattr(storage_quota, "usage_bytes", lambda cid: 0)
    monkeypatch.setattr(us.indexer_manager, "get_indexer", lambda cid: indexer)
    monkeypatch.setattr(us.indexer_manager, "get_documents_path", lambda cid: docs_dir)
    job_updates = []
    monkeypatch.setattr(us.app_db, "update_upload_job", lambda job_id, **kw: job_updates.append(kw))
    monkeypatch.setattr(us.collection_service, "add_document", lambda cid, did: None)

    service = us.UploadService()
    service._cancel_flags[7] = False
    service._run_text_index(7, build_document("big", text="x" * 200), "c1")

    summary = json.loads([u for u in job_updates if u.get("status") == "completed"][-1]["result_summary"])
    assert summary["documents_processed"] == 0
    assert "storage limit" in summary["failed_files"][0]["error"]
    assert not docs_dir.exists() or list(docs_dir.iterdir()) == []
    assert storage_quota.reserved_bytes("c1") == 0


# ── the endpoint ──────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from api import deps
    from services.app_database import SQLiteBackend, app_db
    from services.collection_service import collection_service
    from services.indexer_manager import indexer_manager
    from tests.test_governance import _make_indexer
    import main

    monkeypatch.setattr(app_db, "db_path", tmp_path / "app.db")
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    monkeypatch.setattr(collection_service, "base_dir", tmp_path / "collections")
    monkeypatch.setitem(indexer_manager._indexers, "default", _make_indexer(tmp_path))
    monkeypatch.setattr(indexer_manager, "get_documents_path", lambda cid: tmp_path / "docs")
    monkeypatch.setattr(deps, "_initialized", True)
    return TestClient(main.app)


def test_endpoint_refuses_an_empty_note(client):
    r = client.post("/documents/text", json={"title": "Empty", "html": "<p><br></p>"})
    assert r.status_code == 400
    assert "no text" in r.json()["detail"]


def test_endpoint_refuses_a_note_over_the_storage_cap(client, monkeypatch):
    from services import storage_quota
    monkeypatch.setattr(config.settings, "collection_storage_limit_bytes", 50)
    monkeypatch.setattr(storage_quota, "usage_bytes", lambda cid: 0)
    r = client.post("/documents/text", json={"title": "big", "text": "x" * 200})
    assert r.status_code == 413


def test_endpoint_indexes_the_note_and_lists_it_as_text(client):
    r = client.post("/documents/text", json={
        "title": "Travel policy",
        "html": "<h1>Policy 12</h1><p>File the request <b>two weeks</b> ahead.</p><script>x()</script>",
    })
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["job_type"] == "index"
    assert body["total_files"] == 1
    assert body["current_file"] == "Travel policy"

    deadline = time.time() + 10
    while time.time() < deadline:
        job = client.get(f"/documents/upload/{body['job_id']}/status").json()
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    assert job["status"] == "completed", job

    docs = client.get("/documents").json()["documents"]
    assert [(d["source_type"], d["filename"]) for d in docs] == [("text", "Travel policy.html")]

    # The saved note is served as text, like any saved HTML; it is the
    # cleaned markup, not what was posted.
    served = client.get(f"/documents/{docs[0]['document_id']}/pdf")
    assert served.status_code == 200
    assert "<script>" not in served.text
    assert "<b>two weeks</b>" in served.text
    assert served.headers["content-type"].startswith("text/plain")

    chunks = client.get(f"/documents/{docs[0]['document_id']}/chunks").json()
    assert "two weeks ahead" in " ".join(" ".join(c["text"].split()) for c in chunks["chunks"])
