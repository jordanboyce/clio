"""Page previews: the picture of a cited page behind a result's preview_url.

services.page_preview draws PDF pages with pdfium and scales image files;
the /documents/{id}/preview endpoint serves them with the same access and
quarantine rules as the file itself; search and chat results carry the URL
only for formats that have a page to draw.
"""

import io
import time

import pytest

from services import page_preview
from services.page_preview import PreviewUnavailable, preview_url, render_preview, supports_preview


@pytest.fixture(autouse=True)
def _fresh_cache():
    page_preview._cache.clear()
    yield
    page_preview._cache.clear()


@pytest.fixture()
def pdf(tmp_path):
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument.new()
    doc.new_page(200, 300)
    doc.new_page(300, 200)
    p = tmp_path / "deck.pdf"
    doc.save(str(p))
    doc.close()
    return p


@pytest.fixture()
def photo(tmp_path):
    from PIL import Image
    p = tmp_path / "whiteboard.png"
    Image.new("RGBA", (1200, 800), (255, 0, 0, 128)).save(p)
    return p


def _size(data):
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        return im.size, im.format


# ── which formats have a preview ──────────────────────────────────────


def test_only_formats_with_a_faithful_page_get_a_url():
    assert supports_preview("pdf")
    assert supports_preview("PNG")
    assert supports_preview("jpeg")
    for fmt in ("docx", "md", "csv", "xlsx", "py", "html", "txt", "mp3", None, ""):
        assert not supports_preview(fmt), fmt
    assert preview_url("http://x", "d1", "ops", 4, "pdf") == "http://x/documents/d1/preview?collection_id=ops&page=4"
    assert preview_url("http://x", "d1", "ops", 0, "png") == "http://x/documents/d1/preview?collection_id=ops&page=1"
    assert preview_url("http://x", "d1", "ops", 4, "docx") is None


# ── rendering ─────────────────────────────────────────────────────────


def test_pdf_page_is_rendered_at_the_requested_width(pdf):
    data, media = render_preview(pdf, "pdf", page_number=1, width=100)
    assert media == "image/png"
    (w, h), fmt = _size(data)
    assert (w, h, fmt) == (100, 150, "PNG")

    # The second page is landscape; the width stays what was asked for.
    data, _ = render_preview(pdf, "pdf", page_number=2, width=120)
    assert _size(data)[0] == (120, 80)


def test_width_is_clamped_to_the_allowed_range(pdf):
    data, _ = render_preview(pdf, "pdf", page_number=1, width=5000)
    assert _size(data)[0][0] == page_preview.MAX_WIDTH
    data, _ = render_preview(pdf, "pdf", page_number=1, width=1)
    assert _size(data)[0][0] == page_preview.MIN_WIDTH


def test_missing_page_is_unavailable_not_an_error(pdf):
    with pytest.raises(PreviewUnavailable, match="2 page"):
        render_preview(pdf, "pdf", page_number=3)
    with pytest.raises(PreviewUnavailable):
        render_preview(pdf, "pdf", page_number=0)


def test_image_is_scaled_flattened_and_ignores_the_page(photo):
    data, media = render_preview(photo, "png", page_number=7, width=300)
    assert media == "image/jpeg"
    (w, h), fmt = _size(data)
    assert (w, h, fmt) == (300, 200, "JPEG")
    # Half-transparent red over white flattens to pink, not black.
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        r, g, b = im.getpixel((10, 10))
    assert r > 240 and 110 < g < 150 and 110 < b < 150


def test_small_image_is_not_upscaled(tmp_path):
    from PIL import Image
    p = tmp_path / "icon.png"
    Image.new("RGB", (40, 30), "blue").save(p)
    data, _ = render_preview(p, "png", width=320)
    assert _size(data)[0] == (40, 30)


def test_unsupported_and_unreadable_files_are_unavailable(tmp_path, pdf):
    with pytest.raises(PreviewUnavailable, match="No page preview"):
        render_preview(tmp_path / "notes.docx", "docx")
    bogus = tmp_path / "bogus.pdf"
    bogus.write_bytes(b"not a pdf")
    with pytest.raises(PreviewUnavailable, match="Could not open"):
        render_preview(bogus, "pdf")
    fake_image = tmp_path / "fake.png"
    fake_image.write_bytes(b"not an image")
    with pytest.raises(PreviewUnavailable, match="Could not read"):
        render_preview(fake_image, "png")


def test_cache_is_keyed_on_the_file_not_its_name(pdf, monkeypatch):
    calls = []
    real = page_preview._render_pdf_page
    monkeypatch.setattr(page_preview, "_render_pdf_page", lambda *a: calls.append(a) or real(*a))
    render_preview(pdf, "pdf", 1, 100)
    render_preview(pdf, "pdf", 1, 100)
    assert len(calls) == 1
    render_preview(pdf, "pdf", 2, 100)
    assert len(calls) == 2

    # A replaced file under the same name is drawn afresh.
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument.new()
    doc.new_page(100, 100)
    time.sleep(0.01)
    doc.save(str(pdf))
    doc.close()
    tag_before = page_preview.etag_for(pdf, 1, 100)
    render_preview(pdf, "pdf", 1, 100)
    assert len(calls) == 3
    assert page_preview.etag_for(pdf, 1, 100) == tag_before  # same file, same tag


def test_cache_evicts_the_oldest_entry():
    cache = page_preview.PreviewCache(max_entries=2)
    cache.put(("a",), (b"a", "image/png"))
    cache.put(("b",), (b"b", "image/png"))
    cache.get(("a",))
    cache.put(("c",), (b"c", "image/png"))
    assert cache.get(("b",)) is None
    assert cache.get(("a",)) is not None
    assert cache.get(("c",)) is not None


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
    indexer = _make_indexer(tmp_path)
    monkeypatch.setitem(indexer_manager._indexers, "default", indexer)
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setattr(indexer_manager, "get_documents_path", lambda cid: docs)
    monkeypatch.setattr(deps, "_initialized", True)
    client = TestClient(main.app)
    client.indexer = indexer
    client.docs = docs
    return client


def _register(client, path, document_id, source_format, **extra):
    """Record a document the way the indexer does, with its file in place."""
    import shutil
    shutil.copy(path, client.docs / path.name)
    client.indexer.vector_store.metadata_store.add_document(
        document_id=document_id, filename=path.name, num_pages=2, num_chunks=1,
        upload_timestamp="2026-01-01T00:00:00", source_format=source_format, **extra,
    )


def test_endpoint_serves_a_pdf_page_with_a_validator(client, pdf):
    _register(client, pdf, "deck1", "pdf")
    r = client.get("/documents/deck1/preview?page=2&width=120")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert _size(r.content)[0] == (120, 80)
    assert r.headers["cache-control"] == "private, max-age=86400"
    etag = r.headers["etag"]
    assert etag.startswith('"')

    again = client.get("/documents/deck1/preview?page=2&width=120", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.content == b""

    other = client.get("/documents/deck1/preview?page=1&width=120")
    assert other.status_code == 200
    assert other.headers["etag"] != etag


def test_endpoint_serves_an_image_document_as_a_jpeg_thumbnail(client, photo):
    _register(client, photo, "wb1", "png")
    r = client.get("/documents/wb1/preview?page=3")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert _size(r.content)[0] == (320, 213)


def test_endpoint_answers_404_for_formats_pages_and_documents_without_a_preview(client, pdf, tmp_path):
    note = tmp_path / "notes.md"
    note.write_text("# Notes\n", encoding="utf-8")
    _register(client, note, "note1", "md")
    assert client.get("/documents/note1/preview").status_code == 404
    assert "No page preview" in client.get("/documents/note1/preview").json()["detail"]

    _register(client, pdf, "deck1", "pdf")
    assert client.get("/documents/deck1/preview?page=9").status_code == 404
    assert client.get("/documents/missing/preview").status_code == 404
    # Validation, not a server error, for a width outside the range.
    assert client.get("/documents/deck1/preview?width=10000").status_code == 422
    assert client.get("/documents/deck1/preview?page=0").status_code == 422


def test_quarantined_document_has_no_preview_for_a_non_reviewer(client, pdf, monkeypatch):
    _register(client, pdf, "deck1", "pdf", policy_status="quarantined")
    from api import deps

    def deny(action):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail=action)
    monkeypatch.setattr(deps, "require_admin", deny)
    assert client.get("/documents/deck1/preview").status_code == 404


def test_search_results_carry_a_preview_url_only_when_there_is_a_page(client, pdf, tmp_path, monkeypatch):
    from models.schemas import SearchResult
    from services.indexing.indexer import DocumentIndexer

    def fake_search(self, *args, **kwargs):
        mk = lambda did, fmt, page: SearchResult(
            filename=f"{did}.{fmt}", page_number=page, text_snippet="passage", similarity_score=0.9,
            document_id=did, chunk_id=f"{did}-c1", pdf_url="", page_url="", source_format=fmt,
        )
        return {"results": [mk("deck1", "pdf", 2), mk("note1", "md", 1), mk("wb1", "png", 1)],
                "synthesis": None, "ai_usage": None}
    monkeypatch.setattr(DocumentIndexer, "search", fake_search)

    r = client.post("/search", json={"query": "anything", "top_k": 3})
    assert r.status_code == 200, r.text
    by_id = {x["document_id"]: x["preview_url"] for x in r.json()["results"]}
    assert by_id["deck1"].endswith("/documents/deck1/preview?collection_id=default&page=2")
    assert by_id["wb1"].endswith("/documents/wb1/preview?collection_id=default&page=1")
    assert by_id["note1"] is None


def test_chat_source_payloads_carry_the_preview_url(monkeypatch):
    from api import chat as chat_api
    from models.schemas import ChatSource, SearchResult

    monkeypatch.setattr(chat_api, "_source_sensitivity", lambda cid, r, cache: "internal")
    r = SearchResult(
        filename="deck.pdf", page_number=3, text_snippet="passage", similarity_score=0.9,
        document_id="deck1", chunk_id="c1", pdf_url="", page_url="", source_format="pdf",
    )
    [payload] = chat_api._source_dicts([(r, "ops")], "http://x")
    assert payload["preview_url"] == "http://x/documents/deck1/preview?collection_id=ops&page=3"
    assert payload["source_format"] == "pdf"
    ChatSource(**payload)  # the schema accepts what the turn produces

    # Cached answers remember the format, so a replayed answer keeps its pictures;
    # entries written before the field existed simply have none.
    entry = {"sources": [
        {"filename": "deck.pdf", "page_number": 3, "text_snippet": "p", "similarity_score": 0.9,
         "document_id": "deck1", "collection_id": "ops", "source_format": "pdf"},
        {"filename": "old.pdf", "page_number": 1, "text_snippet": "p", "similarity_score": 0.9,
         "document_id": "old1", "collection_id": "ops"},
    ]}
    new, old = chat_api._cached_sources(entry, "http://x")
    assert new["preview_url"] == "http://x/documents/deck1/preview?collection_id=ops&page=3"
    assert old["preview_url"] is None
    ChatSource(**new)
    ChatSource(**old)
