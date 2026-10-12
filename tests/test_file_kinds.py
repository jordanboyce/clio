"""File kinds: the sidebar's type chips and filter, and the symbol metadata
that code chunks now keep in the store."""

import hashlib

import numpy as np
import pytest

from services import file_kinds
from services.chunker import TextChunker
from services.document_extractor import DocumentExtractor
from services.indexing import DocumentIndexer
from services.metadata_store import MetadataStore
from services.vector_store import VectorStore

EMBED_DIM = 8


class FakeEmbedder:
    model_name = "fake-embed"
    embedding_dim = EMBED_DIM

    @staticmethod
    def _vec(text):
        digest = hashlib.sha256(text.encode()).digest()
        return [b / 255.0 + 0.01 for b in digest[:EMBED_DIM]]

    def embed_texts(self, texts, progress_callback=None):
        return np.array([self._vec(t) for t in texts], dtype=np.float32)

    def embed_query(self, query):
        return np.array(self._vec(query), dtype=np.float32)


@pytest.fixture
def indexer(tmp_path):
    return DocumentIndexer(
        vector_store=VectorStore(index_dir=tmp_path / "indexes", embedding_dim=EMBED_DIM),
        embedding_service=FakeEmbedder(),
        document_extractor=DocumentExtractor(),
        text_chunker=TextChunker(chunk_size=200, chunk_overlap=40),
    )


def test_kind_for_filename_covers_the_families():
    assert file_kinds.kind_for_filename("app.py") == "code"
    assert file_kinds.kind_for_filename("notes/README.md") == "docs"
    assert file_kinds.kind_for_filename("report.pdf") == "docs"
    assert file_kinds.kind_for_filename("sales.csv") == "data"
    assert file_kinds.kind_for_filename("photo.png") == "media"
    assert file_kinds.kind_for_filename("archive.exe") is None


def test_kind_key_matches_the_sql_expression():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    expr = MetadataStore._DOC_KEY_SQL.format(col="f")
    for name in ["a.py", "Makefile", ".bashrc", "archive.tar.gz", "README", "Report.PDF"]:
        sql_key = conn.execute(f"SELECT {expr} FROM (SELECT ? AS f)", (name,)).fetchone()[0]
        assert sql_key == file_kinds.kind_key_for_filename(name), name


def test_kind_counts_folds_keys_into_families():
    counts = file_kinds.kind_counts({".py": 3, ".ts": 1, ".pdf": 2, ".csv": 1, ".exe": 1})
    assert counts["code"] == 4
    assert counts["docs"] == 2
    assert counts["data"] == 1
    assert counts["other"] == 1


def _add_doc(store, doc_id, filename):
    store.add_document(
        document_id=doc_id, filename=filename, num_pages=1, num_chunks=1,
        upload_timestamp="2026-10-08T00:00:00",
    )


def test_store_filters_and_counts_by_kind(tmp_path):
    store = MetadataStore(tmp_path / "meta.db")
    for i, name in enumerate(["main.py", "util.ts", "guide.pdf", "data.csv", "Makefile"]):
        _add_doc(store, f"d{i}", name)

    by_key = store.count_documents_by_key()
    assert by_key[".py"] == 1 and by_key[".pdf"] == 1 and by_key["makefile"] == 1

    code_keys = file_kinds.keys_for_kind("code")
    code_rows = store.list_documents_page(10, keys=code_keys)
    names = {r["filename"] for r in code_rows}
    assert {"main.py", "util.ts"} <= names
    assert "guide.pdf" not in names and "data.csv" not in names
    assert store.count_documents(keys=code_keys) == len(code_rows)
    assert store.count_documents(keys=file_kinds.keys_for_kind("docs")) == 1
    # the substring filter composes with the kind filter
    assert store.count_documents(q="main", keys=code_keys) == 1
    # an empty key list matches nothing rather than everything
    assert store.count_documents(keys=[]) == 0


def test_code_chunks_keep_symbol_metadata(tmp_path, indexer):
    src = tmp_path / "service.py"
    src.write_text(
        "import os\n\n"
        "class Loader:\n"
        "    def read(self, path):\n"
        "        return open(path).read()\n\n"
        "def helper(x):\n"
        "    return x * 2\n",
        encoding="utf-8",
    )
    meta = indexer.index_document(src, "service.py")
    rows = indexer.vector_store.metadata_store.get_chunks_by_document(meta.document_id)
    assert rows, "code file produced chunks"
    by_symbol = {r.get("symbol_name"): r for r in rows if r.get("symbol_name")}
    assert "Loader" in by_symbol and "helper" in by_symbol
    assert by_symbol["helper"]["symbol_type"] == "function"
    assert by_symbol["helper"]["line_start"] == 7
    assert by_symbol["helper"]["language"] == "python"


def test_documents_endpoint_filters_by_kind(tmp_path, monkeypatch):
    """The paged list honours ?kind= and reports per-kind totals for the chips."""
    from fastapi.testclient import TestClient
    import api.deps
    import api.documents as docs_api
    import main

    store = MetadataStore(tmp_path / "meta.db")
    for i, name in enumerate(["main.py", "guide.pdf", "data.csv"]):
        _add_doc(store, f"d{i}", name)

    class FakeIndexer:
        class vector_store:  # noqa: N801 - mirrors the real attribute path
            metadata_store = store

        def list_documents_page(self, limit, offset=0, q="", keys=None):
            return store.list_documents_page(limit, offset=offset, q=q, keys=keys)

        def count_documents(self, q="", keys=None):
            return store.count_documents(q=q, keys=keys)

        def list_documents(self):
            return store.list_documents()

    monkeypatch.setattr(docs_api, "get_indexer", lambda cid: FakeIndexer())
    monkeypatch.setattr(docs_api.indexer_manager, "get_documents_path", lambda cid: tmp_path)
    monkeypatch.setattr(docs_api.collection_service, "get_collection", lambda cid: None)
    monkeypatch.setattr(api.deps, "_initialized", True)

    client = TestClient(main.app)
    resp = client.get("/documents", params={"collection_id": "default", "limit": 10, "kind": "code"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [d["filename"] for d in body["documents"]] == ["main.py"]
    assert body["total_documents"] == 1
    assert body["kind_counts"]["code"] == 1
    assert body["kind_counts"]["docs"] == 1
    assert body["kind_counts"]["data"] == 1

    resp = client.get("/documents", params={"collection_id": "default", "limit": 10, "kind": "bogus"})
    assert resp.status_code == 400
