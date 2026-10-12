"""Nothing about a missing or undownloadable embedding model may stop the
person from opening the app, browsing sources, or choosing another provider.

A collection whose model cannot be built opens read-only behind a deferred
service; vectors raise EmbeddingUnavailable (503 with the reason) until the
model is there, and the first successful build switches it over in place.
"""

import numpy as np
import pytest

from services.embedder import DeferredEmbeddingService, EmbeddingUnavailable, known_embedding_dim


def test_known_dim_comes_from_the_catalog(monkeypatch):
    assert known_embedding_dim("all-MiniLM-L6-v2", provider="local") == 384
    assert known_embedding_dim("BAAI/bge-base-en-v1.5", provider="local") == 768
    assert known_embedding_dim(provider="openai", model_name="text-embedding-3-small") == 1536
    assert known_embedding_dim("not-a-curated-model", provider="local") is None


def test_known_dim_falls_back_to_the_existing_index(tmp_path):
    import faiss
    index = faiss.IndexIDMap2(faiss.IndexFlatIP(8))
    faiss.write_index(index, str(tmp_path / "faiss.index"))
    assert known_embedding_dim("not-a-curated-model", tmp_path, provider="local") == 8


class _Real:
    model_name = "all-MiniLM-L6-v2"
    embedding_dim = 384
    backend = "fastembed"

    def embed_texts(self, texts, progress_callback=None):
        return np.zeros((len(texts), 384), dtype=np.float32)

    def embed_query(self, q):
        return np.zeros(384, dtype=np.float32)


def test_deferred_service_raises_until_the_factory_succeeds():
    attempts = {"n": 0}

    def factory():
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise RuntimeError("Could not download embedding model: hub unreachable")
        return _Real()

    svc = DeferredEmbeddingService("all-MiniLM-L6-v2", 384, factory, reason="not yet")
    assert svc.embedding_dim == 384 and not svc.available
    with pytest.raises(EmbeddingUnavailable, match="hub unreachable"):
        svc.embed_query("hello")
    # Within the retry window the failure is served from memory: no new attempt.
    with pytest.raises(EmbeddingUnavailable):
        svc.embed_query("hello")
    assert attempts["n"] == 1
    svc._last_attempt = 0.0  # window elapsed
    vec = svc.embed_query("hello")
    assert vec.shape == (384,) and svc.available and svc.backend == "fastembed"
    assert svc.embed_texts(["a", "b"]).shape == (2, 384)


def test_deferred_service_refuses_a_dimension_mismatch():
    class Other(_Real):
        embedding_dim = 768

    svc = DeferredEmbeddingService("x", 384, lambda: Other())
    with pytest.raises(EmbeddingUnavailable, match="re-index"):
        svc.embed_query("q")


@pytest.fixture
def unavailable_model(monkeypatch, tmp_path):
    """Every attempt to build the local model fails, as behind a firewall."""
    from services import indexer_manager as im
    from services.collection_service import collection_service

    def boom(*a, **k):
        raise RuntimeError("Could not download embedding model 'all-MiniLM-L6-v2' from huggingface.co")

    monkeypatch.setattr(im, "create_embedding_service", boom)
    monkeypatch.setattr(im.settings, "embedding_provider", "local")
    monkeypatch.setattr(im.settings, "embedding_model", "all-MiniLM-L6-v2")
    monkeypatch.setattr(collection_service, "base_dir", tmp_path)
    manager = im.IndexerManager()
    return manager


def test_collection_opens_read_only_without_the_model(unavailable_model):
    indexer = unavailable_model.get_indexer("default")
    assert isinstance(indexer.embedding_service, DeferredEmbeddingService)
    assert indexer.vector_store.embedding_dim == 384
    # Read paths work: nothing indexed, nothing to list, no exception.
    assert indexer.list_documents() == []
    assert indexer.vector_store.get_total_chunks() == 0
    with pytest.raises(EmbeddingUnavailable, match="huggingface.co"):
        indexer.embedding_service.embed_query("anything")


def test_api_lists_sources_and_answers_503_on_search(unavailable_model, monkeypatch):
    from fastapi.testclient import TestClient
    import api.deps
    import main

    monkeypatch.setattr(api.deps, "_initialized", True)
    monkeypatch.setattr(api.deps, "indexer_manager", unavailable_model, raising=False)
    import api.documents as docs_api
    import api.search as search_api
    monkeypatch.setattr(docs_api, "get_indexer", lambda cid="default": unavailable_model.get_indexer(cid))
    monkeypatch.setattr(search_api, "get_indexer", lambda cid="default": unavailable_model.get_indexer(cid))
    monkeypatch.setattr(docs_api.indexer_manager, "get_documents_path", lambda cid: unavailable_model and __import__("pathlib").Path("/tmp"))

    client = TestClient(main.app)
    listing = client.get("/documents", params={"collection_id": "default", "limit": 10})
    assert listing.status_code == 200, listing.text
    assert listing.json()["documents"] == []

    resp = client.post("/search", json={"query": "anything", "top_k": 3, "collection_id": "default"})
    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["code"] == "embedding_unavailable"
    assert "huggingface.co" in body["detail"]
    assert resp.headers.get("retry-after") == "20"
