"""Resource limits on ingest and on model-written SQL.

- Zip containers are sized up before a parser opens them (a 125 KB DOCX used
  to unpack to minutes and gigabytes), and a refusal fails that one file of
  an upload job, not the job.
- Office, ODF, EPUB and PDF text parsing runs in a helper process with a
  deadline and a memory cap; a helper that hangs, balloons or dies fails the
  file and the next file gets a fresh one.
- Uploads over MAX_UPLOAD_FILE_MB are refused with 413 before the body is
  read, and per file before staging.
- query_table cannot make huge values (randomblob(5e8) and friends).

The bombs here are small: each is built to trip a limit lowered for the test.
"""

import asyncio
import hashlib
import json
import sqlite3
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

import config
from services import extract_sandbox, format_readers
from services.document_extractor import DocumentExtractor
from services.extract_sandbox import ExtractionLimitExceeded
from services.format_readers import ArchiveLimitError, check_zip_container
from services.structured_store import SQLValidationError, StructuredStore

linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="RLIMIT_AS is Linux-only")


@pytest.fixture()
def limits(monkeypatch):
    """Set parser limits on the Settings object the code reads."""
    def _set(**values):
        for name, value in values.items():
            monkeypatch.setattr(config.settings, name, value)
    return _set


def _docx(path: Path, paragraphs: int = 3, padding: int = 0) -> Path:
    """A real DOCX; ``padding`` empty paragraphs are packed into the body."""
    from docx import Document

    plain = path.with_name("plain-" + path.name)
    doc = Document()
    for i in range(paragraphs):
        doc.add_paragraph(f"Paragraph {i} on groundwater recharge.")
    doc.save(plain)
    with zipfile.ZipFile(plain) as zin, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/document.xml" and padding:
                data = data.replace(b"<w:body>", b"<w:body>" + b"<w:p/>" * padding, 1)
            zout.writestr(item, data, compress_type=zipfile.ZIP_DEFLATED)
    return path


# ── Zip-container preflight ─────────────────────────────────────────────────


def test_ordinary_office_files_pass_the_preflight(tmp_path):
    import pandas as pd
    from pptx import Presentation

    pd.DataFrame({"a": range(50), "b": ["x"] * 50}).to_excel(tmp_path / "t.xlsx", index=False)
    deck = Presentation()
    deck.slides.add_slide(deck.slide_layouts[1]).shapes.title.text = "Recharge"
    deck.save(tmp_path / "d.pptx")
    for path in (_docx(tmp_path / "a.docx"), tmp_path / "t.xlsx", tmp_path / "d.pptx"):
        check_zip_container(path)


def test_highly_compressed_docx_is_refused_before_parsing(tmp_path, monkeypatch):
    bomb = _docx(tmp_path / "bomb.docx", padding=400_000)  # 2.4 MB of <w:p/>, ~20 KB zipped
    assert bomb.stat().st_size < 100_000

    parsed = []
    monkeypatch.setattr(DocumentExtractor, "_extract_docx", lambda self, p: parsed.append(p))
    started = time.perf_counter()
    with pytest.raises(ArchiveLimitError, match="compressed .* to 1") as caught:
        DocumentExtractor().extract_text(bomb)
    assert time.perf_counter() - started < 1
    assert caught.value.code == "archive_too_large"
    assert parsed == []


def test_total_unpacked_size_is_capped(tmp_path, limits):
    limits(archive_max_compression_ratio=0, archive_max_uncompressed_mb=1)
    with pytest.raises(ArchiveLimitError, match="ARCHIVE_MAX_UNCOMPRESSED_MB"):
        check_zip_container(_docx(tmp_path / "big.docx", padding=200_000))


def test_number_of_parts_is_capped(tmp_path, limits):
    limits(archive_max_members=5)
    with pytest.raises(ArchiveLimitError, match="ARCHIVE_MAX_MEMBERS"):
        check_zip_container(_docx(tmp_path / "a.docx"))


def test_xml_elements_are_counted_in_tree_built_formats(tmp_path, limits):
    limits(archive_max_compression_ratio=0, archive_max_xml_elements=10_000)
    with pytest.raises(ArchiveLimitError, match="ARCHIVE_MAX_XML_ELEMENTS"):
        check_zip_container(_docx(tmp_path / "many.docx", padding=20_000))


def test_odf_and_epub_go_through_the_same_preflight(tmp_path, limits):
    limits(archive_max_members=2)
    for name in ("a.odt", "b.epub"):
        path = tmp_path / name
        with zipfile.ZipFile(path, "w") as zf:
            for i in range(3):
                zf.writestr(f"part{i}.xml", "<x/>")
        with pytest.raises(ArchiveLimitError, match="parts"):
            DocumentExtractor().extract_text(path)


def test_spreadsheet_tabular_path_is_preflighted(tmp_path, limits):
    import pandas as pd

    pd.DataFrame({"a": range(10)}).to_excel(tmp_path / "t.xlsx", index=False)
    limits(archive_max_members=2)
    with pytest.raises(ArchiveLimitError):
        DocumentExtractor().extract_tabular_sheets(tmp_path / "t.xlsx")


def test_a_non_zip_office_file_gets_a_clear_error(tmp_path):
    path = tmp_path / "fake.docx"
    path.write_bytes(b"not a zip at all")
    with pytest.raises(ValueError, match="not a valid Word file"):
        DocumentExtractor().extract_text(path)


class _FakeEmbedder:
    model_name = "fake-embed"
    embedding_dim = 8

    def embed_texts(self, texts, progress_callback=None):
        if progress_callback:
            progress_callback(len(texts), len(texts))
        return np.array(
            [[b / 255.0 + 0.01 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts],
            dtype=np.float32,
        )

    def embed_query(self, query):
        return self.embed_texts([query])[0]


def test_a_bomb_fails_its_own_file_and_the_job_carries_on(tmp_path, monkeypatch):
    import services.upload_service as us
    from services.chunker import TextChunker
    from services.indexing import DocumentIndexer
    from services.vector_store import VectorStore

    indexer = DocumentIndexer(
        vector_store=VectorStore(index_dir=tmp_path / "indexes", embedding_dim=8),
        embedding_service=_FakeEmbedder(),
        document_extractor=DocumentExtractor(),
        text_chunker=TextChunker(chunk_size=200, chunk_overlap=40),
    )
    good = [_docx(tmp_path / f"good{i}.docx", paragraphs=4) for i in range(2)]
    bomb = _docx(tmp_path / "bomb.docx", padding=400_000)
    updates = []
    monkeypatch.setattr(us.app_db, "update_upload_job", lambda job_id, **kw: updates.append(kw))
    monkeypatch.setattr(us.collection_service, "add_document", lambda cid, doc_id: None)
    monkeypatch.setattr(us.indexer_manager, "get_indexer", lambda collection_id: indexer)

    service = us.UploadService()
    service._cancel_flags[7] = False
    service._run_local_index(7, [str(good[0]), str(bomb), str(good[1])], "c", copy_to_library=False)

    final = updates[-1]
    assert final["status"] == "completed"
    summary = json.loads(final["result_summary"])
    assert summary["documents_processed"] == 2
    assert [f["filename"] for f in summary["failed_files"]] == ["bomb.docx"]
    assert summary["failed_files"][0]["code"] == "archive_too_large"


# ── Helper process ──────────────────────────────────────────────────────────


@pytest.fixture()
def no_preflight(limits):
    """Let a padded DOCX reach the parser, to exercise the helper's own limits."""
    limits(archive_max_compression_ratio=0, archive_max_xml_elements=0, archive_max_uncompressed_mb=0,
           extract_isolation=True)


def test_isolated_extraction_matches_in_process(tmp_path, limits):
    import pandas as pd

    doc = _docx(tmp_path / "a.docx", paragraphs=25)
    pd.DataFrame({"n": [1, 2, 3], "s": ["x", None, "z"]}).to_excel(tmp_path / "t.xlsx", index=False)
    ex = DocumentExtractor()
    limits(extract_isolation=True)
    isolated = (ex.extract_text(doc).page_texts, ex.extract_tabular_sheets(tmp_path / "t.xlsx"))
    limits(extract_isolation=False)
    local = (ex.extract_text(doc).page_texts, ex.extract_tabular_sheets(tmp_path / "t.xlsx"))
    assert isolated == local
    assert list(isolated[0]) == [1, 2, 3]


def test_a_parse_that_runs_too_long_is_stopped(tmp_path, limits, no_preflight):
    slow = _docx(tmp_path / "slow.docx", padding=1_000_000)  # several seconds in python-docx
    limits(extract_timeout_seconds=1, extract_memory_limit_mb=0)
    started = time.perf_counter()
    with pytest.raises(ExtractionLimitExceeded, match="longer than 1 seconds") as caught:
        DocumentExtractor().extract_text(slow)
    assert time.perf_counter() - started < 5
    assert caught.value.code == "extraction_limit"
    # The next file gets a working helper.
    assert DocumentExtractor().extract_text(_docx(tmp_path / "ok.docx")).page_texts


@linux_only
def test_a_parse_that_needs_too_much_memory_is_stopped(tmp_path, limits, no_preflight):
    heavy = _docx(tmp_path / "heavy.docx", padding=1_000_000)  # ~150 MB as an lxml tree
    limits(extract_timeout_seconds=60, extract_memory_limit_mb=64)
    with pytest.raises(ExtractionLimitExceeded, match="more than 64 MB of memory"):
        DocumentExtractor().extract_text(heavy)
    assert DocumentExtractor().extract_text(_docx(tmp_path / "ok.docx")).page_texts


def test_a_helper_that_dies_fails_only_that_file(tmp_path, limits, monkeypatch):
    limits(extract_isolation=True)
    real_call = extract_sandbox._Helper.call

    def die_first(self, task, path, timeout, args=None):
        self.proc.kill()
        self.proc.wait()
        return real_call(self, task, path, timeout, args)

    monkeypatch.setattr(extract_sandbox._Helper, "call", die_first)
    with pytest.raises(ExtractionLimitExceeded, match="parser stopped"):
        DocumentExtractor().extract_text(_docx(tmp_path / "a.docx"))
    monkeypatch.setattr(extract_sandbox._Helper, "call", real_call)
    assert DocumentExtractor().extract_text(_docx(tmp_path / "b.docx")).page_texts


def test_parse_errors_keep_their_type(tmp_path, limits):
    limits(extract_isolation=True)
    path = tmp_path / "broken.pptx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("hello.txt", "not a deck")
    with pytest.raises(Exception) as caught:
        DocumentExtractor().extract_text(path)
    assert not isinstance(caught.value, ExtractionLimitExceeded)


def test_isolation_off_parses_in_process(tmp_path, limits, monkeypatch):
    limits(extract_isolation=False)
    monkeypatch.setattr(extract_sandbox._Helper, "__init__", lambda *a, **k: pytest.fail("helper started"))
    assert DocumentExtractor().extract_text(_docx(tmp_path / "a.docx")).page_texts


# ── Upload size ─────────────────────────────────────────────────────────────


def _asgi_call(app, path, headers, chunks):
    """Drive an ASGI app with a body sent in ``chunks``; returns (status, body, read)."""
    sent, read = [], []

    async def receive():
        if chunks:
            body = chunks.pop(0)
            read.append(len(body))
            return {"type": "http.request", "body": body, "more_body": bool(chunks)}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": path, "headers": headers}
    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body, read


async def _drain_and_ok(scope, receive, send):
    while (await receive()).get("more_body"):
        pass
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def test_declared_oversized_upload_is_refused_before_the_body_is_read(limits):
    from middleware.upload_limit import UploadSizeLimit

    limits(max_upload_file_mb=1)
    status, body, read = _asgi_call(
        UploadSizeLimit(_drain_and_ok), "/documents/upload-staged",
        [(b"content-length", str(5 * 1024 * 1024).encode())], [b"x" * 1024],
    )
    assert status == 413
    assert json.loads(body)["code"] == "upload_too_large"
    assert "1 MB" in json.loads(body)["detail"]
    assert read == []


def test_undeclared_oversized_upload_is_stopped_while_it_streams(limits):
    from middleware.upload_limit import UploadSizeLimit

    limits(max_upload_file_mb=1)
    chunks = [b"x" * (512 * 1024)] * 8  # 4 MB, no Content-Length
    status, _, read = _asgi_call(UploadSizeLimit(_drain_and_ok), "/api/v1/collections/c/documents", [], chunks)
    assert status == 413
    assert sum(read) <= 3 * 512 * 1024 + 1024 * 1024


def test_other_routes_and_small_uploads_pass(limits):
    from middleware.upload_limit import UploadSizeLimit

    limits(max_upload_file_mb=1)
    big = [(b"content-length", str(5 * 1024 * 1024).encode())]
    assert _asgi_call(UploadSizeLimit(_drain_and_ok), "/collections/import", big, [b"x"])[0] == 200
    assert _asgi_call(UploadSizeLimit(_drain_and_ok), "/documents/upload", [], [b"x" * 1000])[0] == 200


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from api import deps
    from services.app_database import SQLiteBackend, app_db
    from services.chunker import TextChunker
    from services.collection_service import collection_service
    from services.indexer_manager import indexer_manager
    from services.indexing import DocumentIndexer
    from services.vector_store import VectorStore

    monkeypatch.setattr(app_db, "db_path", tmp_path / "app.db")
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    monkeypatch.setattr(collection_service, "base_dir", tmp_path / "collections")
    indexer = DocumentIndexer(
        vector_store=VectorStore(index_dir=tmp_path / "indexes", embedding_dim=8),
        embedding_service=_FakeEmbedder(),
        document_extractor=DocumentExtractor(),
        text_chunker=TextChunker(chunk_size=200, chunk_overlap=40),
    )
    monkeypatch.setitem(indexer_manager._indexers, "default", indexer)
    monkeypatch.setattr(deps, "_initialized", True)
    import main

    return TestClient(main.app)


def test_http_upload_over_the_cap_is_413(client, limits):
    limits(max_upload_file_mb=1)
    r = client.post("/documents/upload", files={"files": ("big.txt", b"a" * (3 * 1024 * 1024), "text/plain")})
    assert r.status_code == 413
    assert "MAX_UPLOAD_FILE_MB" in r.json()["detail"]


def test_a_file_just_over_the_cap_is_refused_per_file(client, limits):
    """Inside the request allowance (cap + multipart framing), but over the cap."""
    limits(max_upload_file_mb=1)
    over = ("over.txt", b"word " * (220 * 1024), "text/plain")  # 1.1 MB
    ok = ("ok.txt", b"Groundwater recharge depends on flooding.", "text/plain")

    staged = client.post("/documents/upload-staged", files=[("files", over), ("files", ok)])
    assert staged.status_code == 202, staged.text
    assert [f["filename"] for f in staged.json()["staged"]] == ["ok.txt"]
    assert "MAX_UPLOAD_FILE_MB" in staged.json()["failed"][0]["error"]

    direct = client.post("/documents/upload", files=[("files", over)])
    assert direct.status_code == 413
    assert "MAX_UPLOAD_FILE_MB" in direct.json()["detail"]


# ── query_table ─────────────────────────────────────────────────────────────


@pytest.fixture()
def store(tmp_path):
    db = tmp_path / "metadata.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE csv_data_t(__row_number, region, amount)")
    conn.executemany("INSERT INTO csv_data_t VALUES(?,?,?)",
                     [(i, "NSEW"[i % 4], i * 1.5) for i in range(200)])
    conn.commit()
    conn.close()
    s = StructuredStore.__new__(StructuredStore)
    s.db_path = db
    return s


@pytest.mark.parametrize("sql, message", [
    ("SELECT length(randomblob(500000000))", "randomblob"),
    ("SELECT length(zeroblob(500000000))", "zeroblob"),
    ("SELECT length(replace(printf('%.*c', 900000, 'x'), 'x', 'xxxxxxxxxx'))", "larger than 1 MB"),
    ("WITH RECURSIVE r(s) AS (SELECT 'x' UNION ALL SELECT s || s FROM r LIMIT 40) "
     "SELECT max(length(s)) FROM r", "larger than 1 MB"),
])
def test_queries_cannot_make_huge_values(store, sql, message):
    started = time.perf_counter()
    with pytest.raises(SQLValidationError, match=message):
        store.execute_query(sql)
    assert time.perf_counter() - started < 1


def test_results_are_capped_in_bytes(store):
    result = store.execute_query("SELECT printf('%.*c', 900000, 'x') FROM csv_data_t")
    assert result["truncated"] is True
    assert result["row_count"] < 20


@pytest.mark.parametrize("sql, first", [
    ("SELECT region, count(*), sum(amount) FROM csv_data_t GROUP BY region ORDER BY region",
     ["E", 50, 7500.0]),
    ("SELECT region, amount, rank() OVER (PARTITION BY region ORDER BY amount DESC) "
     "FROM csv_data_t ORDER BY region, 3 LIMIT 1", ["E", 297.0, 1]),
    ("WITH t AS (SELECT region, avg(amount) a FROM csv_data_t GROUP BY region) "
     "SELECT region FROM t ORDER BY a DESC LIMIT 1", ["W"]),
    ("SELECT printf('%.2f', sum(amount)), length(group_concat(region, '')) FROM csv_data_t",
     ["29850.00", 200]),
])
def test_analytics_queries_still_work(store, sql, first):
    assert store.execute_query(sql)["rows"][0] == first
