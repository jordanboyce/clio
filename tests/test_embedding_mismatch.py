"""A model whose vectors do not fit the collection's index.

The person sees "completed with errors" and no sources unless the failure is
typed all the way up: the exception carries a stable code and both
dimensions, a bulk job refuses up front instead of failing every file, the
job record keeps the code so the UI can offer a re-index, and the collection
reports it through its own health endpoint.
"""

import json

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.embedder import (
    DeferredEmbeddingService,
    EmbeddingDimensionMismatch,
    EmbeddingUnavailable,
    ensure_embedding_ready,
)


class _Model:
    def __init__(self, dim, name="llama-nemotron-embed"):
        self.embedding_dim = dim
        self.model_name = name
        self.backend = "fastembed"

    def embed_texts(self, texts, progress_callback=None):
        return np.zeros((len(texts), self.embedding_dim), dtype=np.float32)

    def embed_query(self, q):
        return np.zeros(self.embedding_dim, dtype=np.float32)


def _mismatched():
    return DeferredEmbeddingService("llama-nemotron-embed", 384, lambda: _Model(2048))


# ── the exception ──────────────────────────────────────────────────────

def test_mismatch_is_typed_and_carries_both_dimensions():
    svc = _mismatched()
    with pytest.raises(EmbeddingDimensionMismatch) as info:
        svc.embed_query("q")
    e = info.value
    assert e.code == "embedding_mismatch"
    assert (e.model_name, e.model_dim, e.index_dim) == ("llama-nemotron-embed", 2048, 384)
    assert "2048-dimensional" in str(e) and "expects 384" in str(e) and "re-index" in str(e)
    assert isinstance(e, EmbeddingUnavailable)       # existing 503 handling still applies


def test_second_call_inside_the_retry_window_keeps_the_type():
    svc = _mismatched()
    for _ in range(3):
        with pytest.raises(EmbeddingDimensionMismatch) as info:
            svc.embed_texts(["x"])
        assert info.value.index_dim == 384


def test_plain_failures_have_their_own_code():
    def boom():
        raise RuntimeError("hub unreachable")

    svc = DeferredEmbeddingService("m", 384, boom)
    for _ in range(2):
        with pytest.raises(EmbeddingUnavailable) as info:
            svc.embed_query("q")
        assert info.value.code == "embedding_unavailable"
        assert not isinstance(info.value, EmbeddingDimensionMismatch)


def test_ensure_ready_is_a_no_op_for_a_working_model():
    ensure_embedding_ready(_Model(384))                              # a real service has no _ensure
    ok = DeferredEmbeddingService("m", 384, lambda: _Model(384))
    ensure_embedding_ready(ok)
    assert ok.available
    with pytest.raises(EmbeddingDimensionMismatch):
        ensure_embedding_ready(_mismatched())


# ── bulk job ───────────────────────────────────────────────────────────

def test_link_job_refuses_up_front_and_keeps_the_code(tmp_path, monkeypatch, caplog):
    import services.upload_service as us
    from tests.test_governance import _make_indexer

    indexer = _make_indexer(tmp_path)
    indexer.embedding_service = _mismatched()
    fetched = []
    monkeypatch.setattr(us, "fetch_link", lambda url: fetched.append(url))
    monkeypatch.setattr(us.indexer_manager, "get_indexer", lambda cid: indexer)
    monkeypatch.setattr(us.indexer_manager, "get_documents_path", lambda cid: tmp_path / "docs")
    updates = []
    monkeypatch.setattr(us.app_db, "update_upload_job", lambda job_id, **kw: updates.append(kw))

    service = us.UploadService()
    service._cancel_flags[9] = False
    with caplog.at_level("ERROR"):
        service._run_link_index(9, ["https://example.com/a", "https://example.com/b"], "c1")

    assert fetched == []                                   # nothing downloaded for a doomed job
    final = [u for u in updates if u.get("status") == "failed"]
    assert final and "2048-dimensional" in final[-1]["error"]
    summary = json.loads(final[-1]["result_summary"])
    assert summary["error_code"] == "embedding_mismatch"
    assert summary["documents_processed"] == 0
    # The server log names the job, collection and error class.
    log = " ".join(r.getMessage() for r in caplog.records)
    assert "job 9" in log and "'c1'" in log and "EmbeddingDimensionMismatch" in log


def test_per_file_failure_records_the_code(tmp_path, monkeypatch):
    """A file that fails with a typed error keeps the code beside its message."""
    import services.upload_service as us
    from tests.test_governance import _make_indexer
    from tests.test_link_indexing import _fetched

    indexer = _make_indexer(tmp_path)

    def embed_fails(*a, **k):
        raise EmbeddingDimensionMismatch("llama-nemotron-embed", 2048, 384)

    monkeypatch.setattr(indexer.embedding_service, "embed_texts", embed_fails)
    monkeypatch.setattr(us, "fetch_link", lambda url: _fetched(url))
    monkeypatch.setattr(us.indexer_manager, "get_indexer", lambda cid: indexer)
    monkeypatch.setattr(us.indexer_manager, "get_documents_path", lambda cid: tmp_path / "docs")
    updates = []
    monkeypatch.setattr(us.app_db, "update_upload_job", lambda job_id, **kw: updates.append(kw))
    monkeypatch.setattr(us.collection_service, "add_document", lambda cid, did: None)

    service = us.UploadService()
    service._cancel_flags[10] = False
    service._run_link_index(10, ["https://example.com/a"], "c1")

    done = [u for u in updates if u.get("status") in ("completed", "failed")][-1]
    summary = json.loads(done["result_summary"])
    assert summary["documents_processed"] == 0
    failed = summary["failed_files"]
    assert failed and failed[0]["code"] == "embedding_mismatch"
    assert failed[0]["filename"] == "https://example.com/a"


# ── health endpoint ────────────────────────────────────────────────────

@pytest.fixture()
def client(monkeypatch):
    import api.collections as col
    from api import deps
    from middleware.user_context import get_current_user_id

    state = {"service": _Model(384), "owner": True}

    class _Idx:
        @property
        def embedding_service(self):
            return state["service"]

    monkeypatch.setattr(col.indexer_manager, "get_indexer", lambda cid: _Idx())
    monkeypatch.setattr(col.collection_service, "get_collection",
                        lambda cid: {"id": cid} if cid == "docs" else None)

    def access(collection_id, user_id, write=False, owner=False):
        if owner and not state["owner"]:
            from fastapi import HTTPException
            raise HTTPException(403, "owner only")
        return "owner"

    monkeypatch.setattr(deps, "require_collection_access", access)
    app = FastAPI()
    app.include_router(col.router)
    app.dependency_overrides[get_current_user_id] = lambda: "u1"
    c = TestClient(app)
    c.state = state
    return c


def test_health_ok(client):
    assert client.get("/api/collections/docs/embedding-health").json() == {"ok": True, "collection_id": "docs"}


def test_health_reports_mismatch_with_both_dimensions(client):
    client.state["service"] = _mismatched()
    body = client.get("/api/collections/docs/embedding-health").json()
    assert body["ok"] is False and body["code"] == "embedding_mismatch"
    assert (body["model"], body["model_dim"], body["index_dim"]) == ("llama-nemotron-embed", 2048, 384)
    assert body["fix"] == "reindex" and body["can_reindex"] is True


def test_health_hides_reindex_from_non_owners(client):
    client.state["service"] = _mismatched()
    client.state["owner"] = False
    body = client.get("/api/collections/docs/embedding-health").json()
    assert body["code"] == "embedding_mismatch" and body["can_reindex"] is False


def test_health_reports_an_unavailable_model_as_not_fixable_by_reindex(client):
    def boom():
        raise RuntimeError("hub unreachable")

    client.state["service"] = DeferredEmbeddingService("m", 384, boom)
    body = client.get("/api/collections/docs/embedding-health").json()
    assert body["ok"] is False and body["code"] == "embedding_unavailable"
    assert body["fix"] == "model" and "can_reindex" not in body and "hub unreachable" in body["message"]


def test_health_unknown_collection_is_404(client):
    assert client.get("/api/collections/nope/embedding-health").status_code == 404


# ── opening a collection whose index no longer matches the model ───────

@pytest.fixture()
def manager_factory(monkeypatch, tmp_path):
    """IndexerManagers over one data dir, each building a model of a chosen size."""
    from services import indexer_manager as im
    from services.collection_service import collection_service

    monkeypatch.setattr(im.settings, "embedding_provider", "local")
    monkeypatch.setattr(im.settings, "embedding_model", "all-MiniLM-L6-v2")
    monkeypatch.setattr(collection_service, "base_dir", tmp_path)

    def make(dim):
        monkeypatch.setattr(im, "create_embedding_service", lambda *a, **k: _Model(dim))
        return im.IndexerManager()

    return make


def test_collection_with_an_older_index_opens_read_only_and_reports_the_mismatch(manager_factory):
    old = manager_factory(384).get_indexer("default")
    old.vector_store.save()                                  # a 384-dim index now exists on disk

    # The model now in use writes 2048-dim vectors. This used to fail the whole
    # open with a bare RuntimeError, leaving the collection unusable.
    indexer = manager_factory(2048).get_indexer("default")

    assert isinstance(indexer.embedding_service, DeferredEmbeddingService)
    assert indexer.vector_store.embedding_dim == 384
    assert indexer.list_documents() == []                    # reads still work
    with pytest.raises(EmbeddingDimensionMismatch) as info:
        ensure_embedding_ready(indexer.embedding_service)
    assert (info.value.model_dim, info.value.index_dim) == (2048, 384)


def test_matching_model_still_opens_normally(manager_factory):
    manager_factory(384).get_indexer("default").vector_store.save()
    indexer = manager_factory(384).get_indexer("default")
    assert not isinstance(indexer.embedding_service, DeferredEmbeddingService)
    ensure_embedding_ready(indexer.embedding_service)


def test_reindex_resolves_it(manager_factory):
    """After the index is rebuilt at the new size the same model fits."""
    manager_factory(384).get_indexer("default").vector_store.save()
    manager = manager_factory(2048)
    manager.get_indexer("default")
    # What a re-index leaves behind: an index of the new size (discarded and rebuilt).
    from services.vector_store import VectorStore
    from services.collection_service import collection_service
    store = VectorStore(collection_service.get_indexes_path("default"), embedding_dim=2048,
                        discard_mismatched_index=True)
    store.save()
    store.close()
    healed = manager.rebuild_indexer("default")
    assert not isinstance(healed.embedding_service, DeferredEmbeddingService)
    assert healed.vector_store.embedding_dim == 2048
