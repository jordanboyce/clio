"""A collection whose own model is missing, or whose vectors came from
another model (work order B8).

The fourth row of the model matrix: a :bge image on a data volume made with
MiniLM, offline. Both models are 384-dimensional, so nothing caught a query
embedded with bge searching MiniLM vectors. Now the saved vectors' model is
checked when a collection opens, the collection says which model it needs,
and it can be moved from the UI (copy, or re-index with the model).
"""

from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import HTTPException

import config
from services import indexer_manager as im
from services.embedder import DeferredEmbeddingService, EmbeddingModelMismatch, EmbeddingUnavailable


class FakeService:
    def __init__(self, model, signature, dim=384):
        self.model_name = model
        self.signature = signature
        self.embedding_dim = dim
        self.backend = "fastembed"

    def embed_texts(self, texts, progress_callback=None):
        return np.ones((len(texts), self.embedding_dim), dtype="float32")

    def embed_query(self, query):
        return np.ones(self.embedding_dim, dtype="float32")


def _write_index_with(tmp_path, signature, model):
    """An index holding one vector, recorded as written by ``model``."""
    import faiss

    from services.metadata_store import MetadataStore

    indexes = tmp_path / "indexes"
    indexes.mkdir(parents=True, exist_ok=True)
    index = faiss.IndexIDMap2(faiss.IndexFlatIP(384))
    index.add_with_ids(np.ones((1, 384), dtype="float32"), np.array([1], dtype="int64"))
    faiss.write_index(index, str(indexes / "faiss.index"))
    MetadataStore(indexes / "metadata.db").set_index_info({"signature": signature, "model": model, "dimension": 384})
    return indexes


def test_vectors_of_another_model_of_the_same_size_are_refused(tmp_path, monkeypatch):
    indexes = _write_index_with(tmp_path, "local:all-MiniLM-L6-v2", "all-MiniLM-L6-v2")
    monkeypatch.setattr(im.collection_service, "get_indexes_path", lambda cid: indexes)
    monkeypatch.setattr(im.collection_service, "get_documents_path", lambda cid: tmp_path / "docs")
    bge = FakeService("BAAI/bge-small-en-v1.5", "local:BAAI/bge-small-en-v1.5")
    monkeypatch.setattr(im.indexer_manager, "_reach_embedding_service", lambda model: bge)

    indexer = im.indexer_manager._create_indexer({"id": "c1", "embedding_model": "BAAI/bge-small-en-v1.5"})
    service = indexer.embedding_service
    real = getattr(service, "_inner", service)
    with pytest.raises(EmbeddingModelMismatch) as err:
        (real if isinstance(real, DeferredEmbeddingService) else service).embed_query("x")
    assert err.value.code == "embedding_model_mismatch"
    assert err.value.stored_model == "all-MiniLM-L6-v2"


def test_the_same_model_opens_as_before(tmp_path, monkeypatch):
    indexes = _write_index_with(tmp_path, "local:all-MiniLM-L6-v2", "all-MiniLM-L6-v2")
    monkeypatch.setattr(im.collection_service, "get_indexes_path", lambda cid: indexes)
    monkeypatch.setattr(im.collection_service, "get_documents_path", lambda cid: tmp_path / "docs")
    minilm = FakeService("all-MiniLM-L6-v2", "local:all-MiniLM-L6-v2")
    monkeypatch.setattr(im.indexer_manager, "_reach_embedding_service", lambda model: minilm)
    indexer = im.indexer_manager._create_indexer({"id": "c2", "embedding_model": "all-MiniLM-L6-v2"})
    assert indexer.embedding_service.embed_query("x").shape == (384,)


def test_a_deferred_model_that_arrives_as_another_model_is_refused_too():
    stored = {"signature": "local:all-MiniLM-L6-v2", "model": "all-MiniLM-L6-v2"}

    def checked(service):
        if service.signature != stored["signature"]:
            raise EmbeddingModelMismatch(stored["model"], service.model_name)
        return service
    deferred = DeferredEmbeddingService("x", 384, factory=lambda: checked(FakeService("bge", "local:bge")))
    with pytest.raises(EmbeddingModelMismatch):
        deferred.embed_query("x")
    # The typed reason survives the retry window, not a bare "unavailable".
    with pytest.raises(EmbeddingModelMismatch):
        deferred.embed_query("x")


# ── the API ────────────────────────────────────────────────────────────

@pytest.fixture()
def api(monkeypatch):
    import api.collections as collections_api

    monkeypatch.setattr(config.settings, "embedding_provider", "local")
    monkeypatch.setattr(config.settings, "embedding_model", "BAAI/bge-small-en-v1.5")
    return collections_api


def test_health_says_which_model_the_collection_needs_and_offers_a_copy(api, monkeypatch):
    import asyncio

    monkeypatch.setattr(api.collection_service, "get_collection",
                        lambda cid: {"id": cid, "embedding_model": "all-MiniLM-L6-v2"})

    class Unavailable:
        embedding_service = SimpleNamespace()
    monkeypatch.setattr(api.indexer_manager, "get_indexer", lambda cid: Unavailable())
    import services.embedder as embedder

    def not_ready(service):
        raise EmbeddingUnavailable("Embedding model 'all-MiniLM-L6-v2' is not on this server")
    monkeypatch.setattr(embedder, "ensure_embedding_ready", not_ready)
    import api.deps as deps
    monkeypatch.setattr(deps, "require_collection_access", lambda *a, **k: "owner")

    out = asyncio.run(api.get_collection_embedding_health("c1", user_id="u"))
    assert out["fix"] == "copy" and out["can_copy"] is True
    assert out["collection_model"] == "all-MiniLM-L6-v2" and out["server_model"] == "BAAI/bge-small-en-v1.5"


def test_the_list_marks_a_collection_whose_model_is_not_here(api, monkeypatch):
    import services.embedder as embedder

    monkeypatch.setattr(embedder, "local_model_cached", lambda name, backend=None: name == "BAAI/bge-small-en-v1.5")
    assert api._own_model_missing({"embedding_model": "all-MiniLM-L6-v2"}) is True
    assert api._own_model_missing({"embedding_model": "BAAI/bge-small-en-v1.5"}) is False
    monkeypatch.setattr(config.settings, "embedding_provider", "ollama")
    assert api._own_model_missing({"embedding_model": "all-MiniLM-L6-v2"}) is False


def test_relabelling_a_collection_that_holds_vectors_is_refused(api, monkeypatch, tmp_path):
    indexes = _write_index_with(tmp_path, "local:all-MiniLM-L6-v2", "all-MiniLM-L6-v2")
    monkeypatch.setattr(api.indexer_manager, "get_indexes_path", lambda cid: indexes)
    monkeypatch.setattr(api.collection_service, "get_collection",
                        lambda cid: {"id": cid, "embedding_model": "all-MiniLM-L6-v2"})
    import api.deps as deps
    monkeypatch.setattr(deps, "require_collection_access", lambda *a, **k: "owner")
    with pytest.raises(HTTPException) as err:
        api.update_collection("c1", {"embedding_model": "BAAI/bge-small-en-v1.5"}, user_id="u")
    assert err.value.status_code == 409 and "re-index" in err.value.detail


def test_a_reindex_with_a_model_relabels_only_after_the_new_index_is_saved(monkeypatch, tmp_path):
    from services import reindex_service as rs

    order = []
    monkeypatch.setattr(rs, "create_embedding_service",
                        lambda collection_model=None, overrides=None: FakeService(collection_model, f"local:{collection_model}"))

    class Store:
        def __init__(self, index_dir, embedding_dim, discard_mismatched_index=False):
            self.metadata_store = SimpleNamespace()

        def clear_index(self):
            order.append("clear")

        def save(self):
            order.append("save")
    monkeypatch.setattr(rs, "VectorStore", Store)
    monkeypatch.setattr(rs, "DocumentIndexer", lambda **kw: SimpleNamespace(collection_id=None))
    service = rs.ReindexService()
    monkeypatch.setattr(service, "_get_documents_to_reindex", lambda store, d: [{"document_id": "d1"}])
    monkeypatch.setattr(service, "_reindex_documents", lambda *a, **k: order.append("embed") or [])
    monkeypatch.setattr(service, "_finish_reindex", lambda *a, **k: None)
    monkeypatch.setattr(rs, "_safe_supersede_marks", lambda store: [])
    monkeypatch.setattr(rs, "_safe_restore_supersede_marks", lambda store, marks: None)
    monkeypatch.setattr(rs.app_db, "update_reindex_job", lambda *a, **k: None)
    monkeypatch.setattr(service, "_relabel", lambda cid, model: order.append(f"relabel:{model}") if model else None)
    service._run_collection_reindex(job_id=1, collection_id="c1", documents_dir=tmp_path, indexes_dir=tmp_path,
                                    embedding_model="BAAI/bge-small-en-v1.5", chunk_size=1000, chunk_overlap=100,
                                    embedding_dim=384, relabel_to="BAAI/bge-small-en-v1.5")
    assert order == ["clear", "embed", "save", "relabel:BAAI/bge-small-en-v1.5"]
