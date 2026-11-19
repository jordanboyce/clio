"""The owner's decision on B2, option (b): a collection indexed before
recipe 2 is told it can search better and offered a re-index, which rebuilds
it from empty under the current recipe."""

import sqlite3

import numpy as np

from services import reindex_service as rs
from services.chunker import TextChunker
from services.document_extractor import DocumentExtractor
from services.indexing import embedding_text
from services.indexing.indexer import DocumentIndexer
from services.vector_store import VectorStore


class _Embedder:
    model_name = "fake"
    embedding_dim = 8

    def __init__(self):
        self.seen = []

    def embed_texts(self, texts, progress_callback=None):
        self.seen.extend(texts)
        return np.ones((len(texts), 8), dtype=np.float32)

    def embed_query(self, text):
        return np.ones(8, dtype=np.float32)


def _legacy_collection(tmp_path):
    """A collection as one indexed before recipes existed: chunks, no recipe."""
    docs, idx = tmp_path / "documents", tmp_path / "indexes"
    docs.mkdir()
    source = docs / "harbor-policy.md"
    source.write_text("# Allowances\n\nThe overnight allowance is 55 dollars.\n", encoding="utf-8")
    ix = DocumentIndexer(vector_store=VectorStore(idx, embedding_dim=8), embedding_service=_Embedder(),
                         document_extractor=DocumentExtractor(), text_chunker=TextChunker(1000, 200))
    ix.index_document(source, source.name, collection_id="c1")
    ix.save_index()
    with sqlite3.connect(idx / "metadata.db") as conn:
        conn.execute("DELETE FROM index_info WHERE key = 'recipe'")
    return docs, idx


def test_an_old_collection_is_offered_and_a_reindex_upgrades_it(tmp_path, monkeypatch):
    docs, idx = _legacy_collection(tmp_path)
    old = DocumentIndexer(vector_store=VectorStore(idx, embedding_dim=8), embedding_service=_Embedder(),
                          document_extractor=DocumentExtractor(), text_chunker=TextChunker(1000, 200))
    assert old.index_recipe == embedding_text.PLAIN

    embedder = _Embedder()
    monkeypatch.setattr(rs, "create_embedding_service", lambda collection_model=None, **k: embedder)
    monkeypatch.setattr(rs.app_db, "update_reindex_job", lambda *a, **k: None)
    service = rs.ReindexService()
    monkeypatch.setattr(service, "_finish_reindex", lambda *a, **k: None)
    service.reload_callback = None
    service._run_collection_reindex(job_id=1, collection_id="c1", documents_dir=docs, indexes_dir=idx,
                                    embedding_model="fake", chunk_size=1000, chunk_overlap=200, embedding_dim=8)

    store = VectorStore(idx, embedding_dim=8)
    assert store.metadata_store.get_index_info()["recipe"] == embedding_text.CURRENT_RECIPE
    assert store.metadata_store.get_total_chunks() > 0
    assert embedder.seen and all(t.startswith("harbor policy\n") for t in embedder.seen)


def test_the_health_check_says_an_old_collection_can_be_upgraded(tmp_path, monkeypatch):
    import asyncio

    from api import collections as api
    import api.deps as deps

    docs, idx = _legacy_collection(tmp_path)
    ix = DocumentIndexer(vector_store=VectorStore(idx, embedding_dim=8), embedding_service=_Embedder(),
                         document_extractor=DocumentExtractor(), text_chunker=TextChunker(1000, 200))
    monkeypatch.setattr(api.indexer_manager, "get_indexer", lambda cid: ix)
    monkeypatch.setattr(api.collection_service, "get_collection", lambda cid: {"id": cid, "embedding_model": "fake"})
    monkeypatch.setattr(deps, "require_collection_access", lambda *a, **k: "owner")
    monkeypatch.setattr("services.embedder.ensure_embedding_ready", lambda service: None)

    health = asyncio.run(api.get_collection_embedding_health("c1", user_id="u"))

    assert health["ok"] is True and health["recipe_upgrade"] is True and health["can_reindex"] is True
    assert health["recipe"] == embedding_text.PLAIN
    # Asking does not change anything.
    assert "recipe" not in ix.vector_store.metadata_store.get_index_info()
