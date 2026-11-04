"""Figures embedded in documents: extracted at index time, stored beside the
collection's documents, served under the same quarantine rules, and listed
on the results that cite their page.

services.figure_extractor pulls pictures out of PDF, DOCX, PPTX and saved
HTML; services.figures stores them, counts them in storage, and builds the
``figures`` payload; the indexer, the bundle exporter and the API tie them
to the document's life.
"""

import base64
import io
import sqlite3
import zipfile

import pytest

import config
from services import figure_extractor, figures
from services.figure_extractor import extract_figures
from tests.test_governance import _make_indexer

SCRATCH_LOGO = (24, 24)


def _png(size, color="blue") -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def chart(tmp_path):
    p = tmp_path / "chart.png"
    p.write_bytes(_png((200, 120), "blue"))
    return p


@pytest.fixture()
def photo(tmp_path):
    p = tmp_path / "photo.png"
    p.write_bytes(_png((160, 90), "green"))
    return p


@pytest.fixture()
def logo(tmp_path):
    p = tmp_path / "logo.png"
    p.write_bytes(_png(SCRATCH_LOGO, "grey"))
    return p


@pytest.fixture()
def docx(tmp_path, chart, photo, logo):
    from docx import Document
    from docx.shared import Inches

    doc = Document()
    doc.add_paragraph("Quarterly report")
    doc.add_picture(str(chart), width=Inches(2))
    doc.add_paragraph("Figure 1: Sales by region")
    doc.add_picture(str(logo))           # too small: a bullet or a logo
    doc.add_picture(str(chart))          # the same chart again: one figure
    for i in range(12):                  # pushes the next picture onto "page" 2
        doc.add_paragraph(f"Paragraph {i} of the narrative.")
    doc.add_picture(str(photo))
    doc.add_paragraph("A closing remark.")
    p = tmp_path / "report.docx"
    doc.save(str(p))
    return p


@pytest.fixture()
def pptx(tmp_path, chart, photo):
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "Revenue"
    pic = slide.shapes.add_picture(str(chart), Inches(1), Inches(2))
    pic.name = "Revenue by quarter"
    slide2 = prs.slides.add_slide(prs.slide_layouts[5])
    slide2.shapes.title.text = "Team"
    slide2.shapes.add_picture(str(photo), Inches(1), Inches(2))
    slide2.shapes.add_picture(str(chart), Inches(4), Inches(2))  # repeat of slide 1's chart
    p = tmp_path / "deck.pptx"
    prs.save(str(p))
    return p


@pytest.fixture()
def pdf(tmp_path, chart, photo, logo):
    import pypdfium2 as pdfium

    def place(doc, page, png_path, x, y):
        from PIL import Image
        img = Image.open(png_path).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        buf.seek(0)
        obj = pdfium.PdfImage.new(doc)
        obj.load_jpeg(buf, inline=True)
        obj.set_matrix(pdfium.PdfMatrix().scale(img.width, img.height).translate(x, y))
        page.insert_obj(obj)

    doc = pdfium.PdfDocument.new()
    page1 = doc.new_page(400, 600)
    place(doc, page1, chart, 50, 400)
    place(doc, page1, logo, 300, 560)
    page1.gen_content()
    page2 = doc.new_page(400, 600)
    place(doc, page2, photo, 50, 300)
    place(doc, page2, chart, 50, 100)     # repeated chart: not a second figure
    page2.gen_content()
    p = tmp_path / "paper.pdf"
    doc.save(str(p))
    doc.close()
    return p


@pytest.fixture()
def html(tmp_path, chart, logo):
    big = base64.b64encode(chart.read_bytes()).decode()
    small = base64.b64encode(logo.read_bytes()).decode()
    p = tmp_path / "page.html"
    p.write_text(
        "<html><head><title>Results</title></head><body>"
        f"<nav><img src='data:image/png;base64,{big}' alt='Site logo'></nav>"
        "<p>Intro text.</p>"
        "<h1>Growth</h1>"
        f"<figure><img src='data:image/png;base64,{big}'><figcaption>Figure 2: Growth by year</figcaption></figure>"
        "<img src='https://example.com/remote.png' alt='Remote picture'>"
        "<h2>Notes</h2>"
        f"<img src='data:image/png;base64,{small}' alt='bullet'>"
        "</body></html>",
        encoding="utf-8",
    )
    return p


# ── extraction per format ─────────────────────────────────────────────


def test_only_formats_with_embedded_pictures_are_read():
    for fmt in ("pdf", "DOCX", "pptx", "html", "htm"):
        assert figure_extractor.supports_figures(fmt), fmt
    for fmt in ("png", "md", "csv", "txt", "py", None, ""):
        assert not figure_extractor.supports_figures(fmt), fmt


def test_docx_figures_follow_the_extractors_pages_and_skip_small_and_repeated_pictures(docx):
    found = extract_figures(docx, "docx")
    assert [(f.page_number, f.position, f.ext, (f.width, f.height)) for f in found] == [
        (1, 1, "png", (200, 120)),
        (2, 1, "png", (160, 90)),
    ]
    assert found[0].caption == "Figure 1: Sales by region"   # the paragraph after the picture
    assert found[0].content_hash and found[0].content_hash != found[1].content_hash
    assert found[0].data.startswith(b"\x89PNG")


def test_pptx_figures_are_one_per_slide_picture_with_the_shape_name_as_caption(pptx):
    found = extract_figures(pptx, "pptx")
    assert [(f.page_number, f.caption) for f in found] == [(1, "Revenue by quarter"), (2, "")]
    assert (found[1].width, found[1].height) == (160, 90)


def test_pdf_figures_come_from_image_objects_with_captions_from_the_page_text(pdf):
    page_texts = {1: "Results\nFigure 1: Sales by region\nMore text", 2: "Figure 2: The team\nFigure 3: Repeat"}
    found = extract_figures(pdf, "pdf", page_texts)
    assert [(f.page_number, f.position, f.ext) for f in found] == [(1, 1, "jpg"), (2, 1, "jpg")]
    assert (found[0].width, found[0].height) == (200, 120)
    assert found[0].caption == "Figure 1: Sales by region"
    # Two captions for one kept picture: no guess.
    assert found[1].caption == ""


def test_html_figures_use_sections_alt_text_and_figcaptions(html, monkeypatch):
    found = extract_figures(html, "html", fetch_remote=False)
    # The nav logo is boilerplate, the 24px bullet is too small, the remote
    # picture is not fetched: one figure, in the section after the h1.
    assert [(f.page_number, f.caption) for f in found] == [(2, "Figure 2: Growth by year")]

    fetched = []

    class Fetched:
        content_type = "image/png"
        body = _png((300, 200), "red")

    monkeypatch.setattr("services.link_fetcher.fetch_link", lambda url, **kw: fetched.append(url) or Fetched())
    found = extract_figures(html, "html", fetch_remote=True)
    assert fetched == ["https://example.com/remote.png"]
    assert [(f.page_number, f.caption) for f in found] == [(2, "Figure 2: Growth by year"), (2, "Remote picture")]


def test_remote_html_images_follow_the_link_indexing_setting(html, monkeypatch):
    calls = []
    monkeypatch.setattr("services.link_fetcher.fetch_link", lambda url, **kw: calls.append(url) or (_ for _ in ()).throw(ValueError))
    monkeypatch.setattr(config.settings, "link_indexing_enabled", False)
    extract_figures(html, "html")
    assert calls == []


def test_captions_are_cleaned_and_filenames_and_generic_names_dropped():
    assert figure_extractor.clean_caption("  Figure 3:\n Sales  ") == "Figure 3: Sales"
    assert figure_extractor.clean_caption("chart.png") == ""
    assert figure_extractor.clean_caption("Picture 12") == ""
    assert figure_extractor.clean_caption("x" * 400).endswith("…")
    assert figure_extractor.captions_in_text("Intro\nFig. 2 - Totals\nfigure 10a Growth\nNot one") == [
        "Fig. 2 - Totals", "figure 10a Growth",
    ]


def test_extraction_never_fails_the_document(tmp_path):
    bogus = tmp_path / "bogus.docx"
    bogus.write_bytes(b"not a docx")
    assert extract_figures(bogus, "docx") == []
    assert extract_figures(tmp_path / "missing.pdf", "pdf") == []


# ── storage, accounting and deletion through the indexer ──────────────


def test_indexed_figures_are_stored_counted_and_deleted_with_the_document(tmp_path, docx):
    indexer = _make_indexer(tmp_path / "col")
    store = indexer.vector_store.metadata_store
    assert indexer.figures_dir == tmp_path / "col" / "figures"

    meta = indexer.index_document(docx, docx.name)
    rows = store.get_figures(meta.document_id)
    assert [(r["figure_number"], r["page_number"], r["path"], r["caption"]) for r in rows] == [
        (1, 1, f"{meta.document_id}/1.png", "Figure 1: Sales by region"),
        (2, 2, f"{meta.document_id}/2.png", None),
    ]
    files = sorted(p.name for p in (indexer.figures_dir / meta.document_id).iterdir())
    assert files == ["1.png", "2.png"]
    figure_bytes = sum(r["byte_size"] for r in rows)
    assert figure_bytes == sum((indexer.figures_dir / r["path"]).stat().st_size for r in rows)
    assert store.get_storage_bytes() == meta.file_size + figure_bytes
    assert store.get_figure_storage_bytes() == figure_bytes
    # No vision provider: no description, no figure chunk.
    assert all(r["description"] is None for r in rows)
    assert all(c["extraction_method"] != "figure" for c in store.get_chunks_by_document(meta.document_id))

    # Re-indexing the same file replaces the set rather than piling up.
    indexer.delete_document(meta.document_id)
    assert store.get_figures(meta.document_id) == []
    assert not (indexer.figures_dir / meta.document_id).exists()
    assert store.get_storage_bytes() == 0

    indexer.index_document(docx, docx.name)
    indexer.index_document(docx, docx.name)
    assert len(store.get_figures(meta.document_id)) == 2
    assert sorted(p.name for p in (indexer.figures_dir / meta.document_id).iterdir()) == ["1.png", "2.png"]


def test_bulk_path_and_clear_index_handle_figures(tmp_path, pptx):
    indexer = _make_indexer(tmp_path / "col")
    store = indexer.vector_store.metadata_store
    prepared = indexer.prepare_document(pptx, pptx.name)
    assert [f.page_number for f in prepared.figures] == [1, 2]
    [meta] = indexer.index_prepared_documents([prepared])
    assert [r["page_number"] for r in store.get_figures(meta.document_id)] == [1, 2]
    assert (indexer.figures_dir / meta.document_id / "1.png").exists()

    indexer.vector_store.clear_index()
    assert store.get_figures(meta.document_id) == []
    assert not indexer.figures_dir.exists()


def test_figure_extraction_can_be_switched_off(tmp_path, docx, monkeypatch):
    monkeypatch.setattr(config.settings, "extract_figures", False)
    indexer = _make_indexer(tmp_path / "col")
    meta = indexer.index_document(docx, docx.name)
    assert indexer.vector_store.metadata_store.get_figures(meta.document_id) == []
    assert not (indexer.figures_dir / meta.document_id).exists()


def test_figures_are_described_and_indexed_when_a_vision_engine_is_configured(tmp_path, docx, monkeypatch):
    class Engine:
        calls = []

        def describe_image_bytes(self, data, media_type="image/png"):
            self.calls.append(media_type)
            return f"A bar chart of sales by region ({len(data)} bytes)."

    indexer = _make_indexer(tmp_path / "col")
    monkeypatch.setattr(indexer.document_extractor, "_get_vision_ocr_engine", lambda: Engine())
    meta = indexer.index_document(docx, docx.name)
    store = indexer.vector_store.metadata_store
    rows = store.get_figures(meta.document_id)
    assert Engine.calls == ["image/png", "image/png"]
    assert all(r["description"].startswith("A bar chart") for r in rows)

    figure_chunks = [c for c in store.get_chunks_by_document(meta.document_id) if c["extraction_method"] == "figure"]
    assert [(c["page_number"], c["chunk_id"]) for c in figure_chunks] == [
        (1, f"{meta.document_id}_p1_f1"), (2, f"{meta.document_id}_p2_f2"),
    ]
    assert figure_chunks[0]["text"].startswith("Figure 1: Sales by region\nA bar chart")
    assert figure_chunks[1]["text"].startswith("Figure 2\nA bar chart")
    assert meta.total_chunks == len(store.get_chunks_by_document(meta.document_id))

    # Found by what it shows.
    hits = indexer.search("bar chart of sales", top_k=3)["results"]
    assert any(h.extraction_method == "figure" and h.document_id == meta.document_id for h in hits)


def test_a_failing_vision_call_leaves_the_figure_without_words(tmp_path, docx, monkeypatch):
    class Engine:
        def describe_image_bytes(self, data, media_type="image/png"):
            raise RuntimeError("provider down")

    indexer = _make_indexer(tmp_path / "col")
    monkeypatch.setattr(indexer.document_extractor, "_get_vision_ocr_engine", lambda: Engine())
    meta = indexer.index_document(docx, docx.name)
    rows = indexer.vector_store.metadata_store.get_figures(meta.document_id)
    assert len(rows) == 2 and all(r["description"] is None for r in rows)


# ── payloads ──────────────────────────────────────────────────────────


def test_refs_group_figures_by_cited_page(tmp_path, docx):
    indexer = _make_indexer(tmp_path / "col")
    store = indexer.vector_store.metadata_store
    meta = indexer.index_document(docx, docx.name)
    refs = figures.refs_for_pages(store, "http://x", "ops", [(meta.document_id, 1), (meta.document_id, 9), ("nope", 1)])
    assert list(refs) == [(meta.document_id, 1)]
    [ref] = refs[(meta.document_id, 1)]
    assert ref == {
        "url": f"http://x/documents/{meta.document_id}/figures/1?collection_id=ops",
        "figure_number": 1, "page_number": 1, "caption": "Figure 1: Sales by region",
        "width": 200, "height": 120, "description": None,
    }
    assert figures.context_note(refs[(meta.document_id, 1)]) == 'Figures on this page: figure 1 "Figure 1: Sales by region"'
    assert figures.context_note(None) == ""
    long = [{"figure_number": 2, "caption": None, "description": "x" * 500}]
    note = figures.context_note(long)
    assert note.startswith("Figures on this page: figure 2 (xxx") and note.endswith("…)")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from api import deps
    from services.app_database import SQLiteBackend, app_db
    from services.collection_service import collection_service
    from services.indexer_manager import indexer_manager
    import main

    monkeypatch.setattr(app_db, "db_path", tmp_path / "app.db")
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    monkeypatch.setattr(collection_service, "base_dir", tmp_path / "collections")
    indexer = _make_indexer(tmp_path / "col")
    monkeypatch.setitem(indexer_manager._indexers, "default", indexer)
    docs = tmp_path / "col" / "documents"
    docs.mkdir()
    monkeypatch.setattr(indexer_manager, "get_documents_path", lambda cid: docs)
    monkeypatch.setattr(deps, "_initialized", True)
    client = TestClient(main.app)
    client.indexer = indexer
    client.docs = docs
    return client


def test_figure_endpoint_serves_the_stored_picture_with_a_validator(client, docx):
    import shutil
    shutil.copy(docx, client.docs / docx.name)
    meta = client.indexer.index_document(client.docs / docx.name, docx.name)

    r = client.get(f"/documents/{meta.document_id}/figures/1")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert r.content == (client.indexer.figures_dir / meta.document_id / "1.png").read_bytes()
    assert r.headers["cache-control"] == "private, max-age=86400"
    etag = r.headers["etag"]
    assert client.get(f"/documents/{meta.document_id}/figures/1", headers={"If-None-Match": etag}).status_code == 304

    assert client.get(f"/documents/{meta.document_id}/figures/3").status_code == 404
    assert client.get(f"/documents/{meta.document_id}/figures/0").status_code == 404
    assert client.get("/documents/missing/figures/1").status_code == 404
    assert client.get(f"/documents/{meta.document_id}/figures/1?collection_id=nope").status_code == 404

    # A row whose file is gone, or whose path points outside the folder, is 404 too.
    (client.indexer.figures_dir / meta.document_id / "2.png").unlink()
    assert client.get(f"/documents/{meta.document_id}/figures/2").status_code == 404
    store = client.indexer.vector_store.metadata_store
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE document_figures SET path = '../documents/report.docx' WHERE figure_number = 1")
    assert client.get(f"/documents/{meta.document_id}/figures/1").status_code == 404


def test_quarantined_documents_figures_are_hidden_from_non_reviewers(client, docx, monkeypatch):
    import shutil
    shutil.copy(docx, client.docs / docx.name)
    meta = client.indexer.index_document(client.docs / docx.name, docx.name)
    store = client.indexer.vector_store.metadata_store
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE documents SET policy_status = 'quarantined' WHERE document_id = ?", (meta.document_id,))
    from api import deps

    def deny(action):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail=action)
    monkeypatch.setattr(deps, "require_admin", deny)
    assert client.get(f"/documents/{meta.document_id}/figures/1").status_code == 404


def test_search_results_list_the_figures_on_the_cited_page(client, docx, monkeypatch):
    import shutil
    from models.schemas import SearchResult
    from services.indexing.indexer import DocumentIndexer

    shutil.copy(docx, client.docs / docx.name)
    meta = client.indexer.index_document(client.docs / docx.name, docx.name)

    def fake_search(self, *args, **kwargs):
        mk = lambda did, page: SearchResult(
            filename="report.docx", page_number=page, text_snippet="passage", similarity_score=0.9,
            document_id=did, chunk_id=f"{did}-{page}", pdf_url="", page_url="", source_format="docx",
        )
        return {"results": [mk(meta.document_id, 1), mk(meta.document_id, 2), mk("other", 1)],
                "synthesis": None, "ai_usage": None}
    monkeypatch.setattr(DocumentIndexer, "search", fake_search)

    r = client.post("/search", json={"query": "sales", "top_k": 3})
    assert r.status_code == 200, r.text
    first, second, other = r.json()["results"]
    assert [f["figure_number"] for f in first["figures"]] == [1]
    assert first["figures"][0]["url"].endswith(f"/documents/{meta.document_id}/figures/1?collection_id=default")
    assert first["figures"][0]["caption"] == "Figure 1: Sales by region"
    assert [f["figure_number"] for f in second["figures"]] == [2]
    assert other["figures"] is None


def test_chat_sources_and_context_carry_the_pages_figures(client, docx, monkeypatch):
    import shutil
    from api import chat as chat_api
    from models.schemas import ChatSource, SearchResult

    shutil.copy(docx, client.docs / docx.name)
    meta = client.indexer.index_document(client.docs / docx.name, docx.name)
    monkeypatch.setattr(chat_api, "_source_sensitivity", lambda cid, r, cache: "internal")
    r = SearchResult(
        filename="report.docx", page_number=1, text_snippet="passage", similarity_score=0.9,
        document_id=meta.document_id, chunk_id="c1", pdf_url="", page_url="", source_format="docx",
    )
    [payload] = chat_api._source_dicts([(r, "default")], "http://x")
    assert [f["figure_number"] for f in payload["figures"]] == [1]
    assert payload["figures"][0]["url"] == f"http://x/documents/{meta.document_id}/figures/1?collection_id=default"
    ChatSource(**payload)

    entry = {"sources": [
        {"filename": "report.docx", "page_number": 1, "text_snippet": "p", "similarity_score": 0.9,
         "document_id": meta.document_id, "collection_id": "default", "source_format": "docx"},
        {"filename": "old.pdf", "page_number": 1, "text_snippet": "p", "similarity_score": 0.9,
         "document_id": "old1", "collection_id": "gone"},
    ]}
    fresh, missing = chat_api._cached_sources(entry, "http://x")
    assert [f["figure_number"] for f in fresh["figures"]] == [1]
    assert missing["figures"] is None
    ChatSource(**fresh)

    page_figures = payload["figures"]
    block = chat_api._context_entry(0, r, page_figures)
    assert block.startswith('[Source 1: report.docx, page 1 — Figures on this page: figure 1 "Figure 1: Sales by region"]\n')
    assert chat_api._context_entry(0, r).startswith("[Source 1: report.docx, page 1]\n")


# ── bundles ───────────────────────────────────────────────────────────


def test_figures_travel_in_collection_exports_and_imports(tmp_path, docx, monkeypatch):
    from services import collection_bundle as cb
    from services.app_database import SQLiteBackend, app_db
    from services.collection_service import collection_service
    from services.indexer_manager import indexer_manager
    from tests.test_collection_bundle import SignedEmbedder

    for s in {id(config.settings): config.settings, id(cb.settings): cb.settings}.values():
        monkeypatch.setattr(s, "data_dir", tmp_path)
        monkeypatch.setattr(s, "embedding_provider", "local")
        monkeypatch.setattr(s, "embedding_model", "fake-embed")
    monkeypatch.setattr(app_db, "db_path", tmp_path / "app.db")
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    monkeypatch.setattr(collection_service, "base_dir", tmp_path / "collections")
    monkeypatch.setattr(indexer_manager, "_indexers", {})
    monkeypatch.setattr(indexer_manager, "_get_embedding_service", lambda model: SignedEmbedder())

    col = collection_service.create_collection(name="Reports", embedding_model="fake-embed")
    cid = col["id"]
    indexer = indexer_manager.get_indexer(cid)
    import shutil
    target = collection_service.get_documents_path(cid) / docx.name
    shutil.copy(docx, target)
    meta = indexer.index_document(target, docx.name, collection_id=cid)
    indexer.save_index()
    assert indexer.figures_dir == collection_service.get_figures_path(cid)

    zip_path, _ = cb.export_collection(cid, include_sources=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            manifest = cb.read_manifest(zip_path)
        assert f"figures/{meta.document_id}/1.png" in names and f"figures/{meta.document_id}/2.png" in names
        assert manifest["version"] == 3 and manifest["contents"]["figures"] == 2
        assert cb.export_preview(cid)["figures_bytes"] > 0

        imported = cb.import_collection(zip_path, owner_id="alice", name="Copy")
    finally:
        zip_path.unlink(missing_ok=True)
    new_cid = imported["id"]
    new_root = collection_service.get_figures_path(new_cid)
    assert sorted(p.name for p in (new_root / meta.document_id).iterdir()) == ["1.png", "2.png"]
    new_store = indexer_manager.get_indexer(new_cid).vector_store.metadata_store
    assert [r["path"] for r in new_store.get_figures(meta.document_id)] == [
        f"{meta.document_id}/1.png", f"{meta.document_id}/2.png",
    ]
    assert new_store.get_storage_bytes() == indexer.vector_store.metadata_store.get_storage_bytes()


def test_a_quarantined_documents_figures_stay_behind_on_export(tmp_path, docx, monkeypatch):
    from services import collection_bundle as cb
    from services.app_database import SQLiteBackend, app_db
    from services.collection_service import collection_service
    from services.indexer_manager import indexer_manager
    from tests.test_collection_bundle import SignedEmbedder

    for s in {id(config.settings): config.settings, id(cb.settings): cb.settings}.values():
        monkeypatch.setattr(s, "data_dir", tmp_path)
        monkeypatch.setattr(s, "embedding_provider", "local")
        monkeypatch.setattr(s, "embedding_model", "fake-embed")
    monkeypatch.setattr(app_db, "db_path", tmp_path / "app.db")
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    monkeypatch.setattr(collection_service, "base_dir", tmp_path / "collections")
    monkeypatch.setattr(indexer_manager, "_indexers", {})
    monkeypatch.setattr(indexer_manager, "_get_embedding_service", lambda model: SignedEmbedder())

    cid = collection_service.create_collection(name="Reports", embedding_model="fake-embed")["id"]
    indexer = indexer_manager.get_indexer(cid)
    import shutil
    target = collection_service.get_documents_path(cid) / docx.name
    shutil.copy(docx, target)
    meta = indexer.index_document(target, docx.name, collection_id=cid)
    note = collection_service.get_documents_path(cid) / "note.txt"
    note.write_text("A plain note that stays. " * 20, encoding="utf-8")
    indexer.index_document(note, note.name, collection_id=cid)
    indexer.save_index()
    with sqlite3.connect(indexer.vector_store.metadata_store.db_path) as conn:
        conn.execute("UPDATE documents SET policy_status = 'quarantined' WHERE document_id = ?", (meta.document_id,))

    zip_path, _ = cb.export_collection(cid)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            assert not any(n.startswith("figures/") for n in zf.namelist())
            assert cb.read_manifest(zip_path)["version"] == 1
            db = tmp_path / "exported.db"
            db.write_bytes(zf.read("indexes/metadata.db"))
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT COUNT(*) FROM document_figures").fetchone()[0] == 0
    finally:
        zip_path.unlink(missing_ok=True)
