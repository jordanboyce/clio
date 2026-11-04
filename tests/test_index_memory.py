"""Filtered search inside FAISS, and releasing idle vectors from memory.

The release is only safe because the store object never goes away and an index
with unsaved vectors is never released, so most of these tests are about the
ways that could go wrong.
"""

import threading

import numpy as np
import pytest

from models.schemas import ChunkMetadata
from services.vector_store import VectorStore

DIM = 8


def _chunks(count, document_id="doc", start=0):
    return [
        ChunkMetadata(
            document_id=document_id,
            filename=f"{document_id}.txt",
            page_number=1,
            chunk_index=i,
            text=f"passage number {i} of {document_id}",
            chunk_id=f"{document_id}_{i}",
        )
        for i in range(start, start + count)
    ]


def _vectors(count, seed=1):
    return np.random.default_rng(seed).standard_normal((count, DIM), dtype=np.float32)


@pytest.fixture()
def store(tmp_path):
    return VectorStore(tmp_path, embedding_dim=DIM)


def _saved_store(tmp_path, count=20):
    store = VectorStore(tmp_path, embedding_dim=DIM)
    store.add_chunks(_chunks(count), _vectors(count))
    store.save()
    return store


# ── Filtered search ─────────────────────────────────────────────────────────


def _brute_force_top(vectors, query, allowed_positions, k):
    normal = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
    scores = normal @ (query / np.linalg.norm(query))
    ranked = sorted(allowed_positions, key=lambda p: scores[p], reverse=True)
    return ranked[:k]


def test_filtered_search_returns_the_true_top_results(store):
    """The whole point of the old scan-everything approach was exactness."""
    vectors = _vectors(400)
    chunks = _chunks(400)
    store.add_chunks(chunks, vectors)
    query = _vectors(1, seed=99)[0]
    allowed_positions = list(range(0, 400, 7))
    allowed = {chunks[p].chunk_id for p in allowed_positions}

    results = store.search(query, top_k=10, allowed_chunk_ids=allowed)

    expected = [chunks[p].chunk_id for p in _brute_force_top(vectors, query, allowed_positions, 10)]
    assert [r.chunk_id for r in results] == expected


def test_filtered_search_with_fewer_matches_than_top_k(store):
    chunks = _chunks(50)
    store.add_chunks(chunks, _vectors(50))
    allowed = {chunks[3].chunk_id, chunks[30].chunk_id}

    results = store.search(_vectors(1, seed=5)[0], top_k=10, allowed_chunk_ids=allowed)

    assert {r.chunk_id for r in results} == allowed


def test_filter_matching_nothing_returns_nothing(store):
    store.add_chunks(_chunks(5), _vectors(5))
    assert store.search(_vectors(1, seed=5)[0], top_k=3, allowed_chunk_ids=set()) == []
    assert store.search(_vectors(1, seed=5)[0], top_k=3, allowed_chunk_ids={"not-a-chunk"}) == []


def test_filtered_hybrid_search_stays_inside_the_filter(store):
    chunks = _chunks(100)
    store.add_chunks(chunks, _vectors(100))
    allowed = {c.chunk_id for c in chunks[:10]}

    results = store.search_hybrid("passage number", _vectors(1, seed=5)[0], top_k=8, allowed_chunk_ids=allowed)

    assert results
    assert {r.chunk_id for r in results} <= allowed


def test_filtered_search_after_a_delete_never_returns_the_deleted(store):
    store.add_chunks(_chunks(10, "keep") + _chunks(10, "gone"), _vectors(20))
    store.delete_document("gone")
    allowed = {f"keep_{i}" for i in range(10)} | {f"gone_{i}" for i in range(10)}

    results = store.search(_vectors(1, seed=5)[0], top_k=20, allowed_chunk_ids=allowed)

    assert len(results) == 10
    assert all(r.document_id == "keep" for r in results)


# ── Releasing idle vectors ──────────────────────────────────────────────────


def test_idle_saved_index_is_released_and_comes_back_identical(tmp_path):
    store = _saved_store(tmp_path)
    query = _vectors(1, seed=5)[0]
    before = [(r.chunk_id, r.similarity_score) for r in store.search(query, top_k=5)]

    assert store.unload_if_idle(0) is True
    assert store.memory_stats() == {"loaded": 0, "vectors": 0, "bytes": 0}

    assert [(r.chunk_id, r.similarity_score) for r in store.search(query, top_k=5)] == before
    assert store.memory_stats()["vectors"] == 20
    assert store.reload_count == 1


def test_recently_used_index_is_kept(tmp_path):
    store = _saved_store(tmp_path)
    store.search(_vectors(1, seed=5)[0], top_k=5)
    assert store.unload_if_idle(3600) is False
    assert store.memory_stats()["loaded"] == 1


def test_unsaved_vectors_are_never_released(tmp_path):
    """Releasing here would silently lose the batch a running job just added."""
    store = _saved_store(tmp_path)
    store.add_chunks(_chunks(5, "new"), _vectors(5, seed=2))

    assert store.unload_if_idle(0) is False
    assert store.memory_stats()["vectors"] == 25

    store.save()
    assert store.unload_if_idle(0) is True


def test_unsaved_delete_is_never_released(tmp_path):
    store = _saved_store(tmp_path)
    store.delete_document("doc")
    assert store.unload_if_idle(0) is False


def test_replacing_the_index_counts_as_unsaved(tmp_path):
    """The collection-import path swaps in a new index and fills it directly."""
    store = _saved_store(tmp_path)
    store.index = store._new_index()
    assert store.unload_if_idle(0) is False


def test_index_that_was_never_saved_is_kept(tmp_path):
    store = VectorStore(tmp_path, embedding_dim=DIM)
    store.add_chunks(_chunks(3), _vectors(3))
    store._dirty = False  # however it got here, there is nothing on disk to return to
    assert store.unload_if_idle(0) is False
    assert store.memory_stats()["vectors"] == 3


def test_empty_never_saved_index_can_go(tmp_path):
    store = VectorStore(tmp_path, embedding_dim=DIM)
    assert store.unload_if_idle(0) is True
    assert store.index.ntotal == 0


def test_a_writer_holding_the_store_loses_nothing(tmp_path):
    """A job keeps its reference across a release and writes on the next batch."""
    store = _saved_store(tmp_path)
    assert store.unload_if_idle(0) is True

    store.add_chunks(_chunks(5, "later"), _vectors(5, seed=2))
    store.save()

    assert VectorStore(tmp_path, embedding_dim=DIM).index.ntotal == 25


def test_save_while_released_writes_nothing(tmp_path):
    """Otherwise every shutdown reads every collection back into memory."""
    store = _saved_store(tmp_path)
    store.unload_if_idle(0)
    stamp = store.index_path.stat().st_mtime_ns

    store.save()

    assert store.memory_stats()["loaded"] == 0
    assert store.index_path.stat().st_mtime_ns == stamp


def test_index_in_use_is_not_released(tmp_path):
    store = _saved_store(tmp_path)
    holding, done = threading.Event(), threading.Event()

    def hold_lock():
        with store._index_lock:
            holding.set()
            done.wait(5)

    thread = threading.Thread(target=hold_lock)
    thread.start()
    holding.wait(5)
    try:
        assert store.unload_if_idle(0) is False
    finally:
        done.set()
        thread.join()
    assert store.memory_stats()["loaded"] == 1


def test_closed_store_stays_closed(tmp_path):
    store = _saved_store(tmp_path)
    store.close()
    assert store.index is None
    assert store.unload_if_idle(0) is False


def test_searches_during_releases_always_get_right_answers(tmp_path):
    store = _saved_store(tmp_path, count=200)
    query = _vectors(1, seed=5)[0]
    expected = [r.chunk_id for r in store.search(query, top_k=5)]
    stop, failures = threading.Event(), []

    def churn():
        # stop.wait yields: a bare spin would hold the GIL against the searchers.
        while not stop.wait(0.001):
            store.unload_if_idle(0)

    def search():
        for _ in range(25):
            found = [r.chunk_id for r in store.search(query, top_k=5)]
            if found != expected:
                failures.append(found)

    churner = threading.Thread(target=churn)
    searchers = [threading.Thread(target=search) for _ in range(4)]
    churner.start()
    for t in searchers:
        t.start()
    for t in searchers:
        t.join()
    stop.set()
    churner.join()

    assert not failures


# ── The sweep across collections ────────────────────────────────────────────


class _Indexer:
    def __init__(self, store):
        self.vector_store = store


@pytest.fixture()
def manager(tmp_path, monkeypatch):
    from config import settings
    from services.indexer_manager import IndexerManager

    instance = IndexerManager.__new__(IndexerManager)
    instance._indexers = {"a": _Indexer(_saved_store(tmp_path / "a")), "b": _Indexer(_saved_store(tmp_path / "b"))}
    instance.index_unload_count = 0
    monkeypatch.setattr(settings, "index_idle_unload_seconds", 1)
    for indexer in instance._indexers.values():
        indexer.vector_store._last_used -= 10
    return instance


def test_sweep_releases_idle_collections_and_counts_them(manager):
    assert manager.index_memory_stats()["loaded"] == 2
    manager._indexers["b"].vector_store.search(_vectors(1, seed=5)[0], top_k=2)

    assert manager.unload_idle_indexes() == 1

    stats = manager.index_memory_stats()
    assert stats["open"] == 2
    assert stats["loaded"] == 1
    assert stats["vectors"] == 20
    assert stats["unloads"] == 1


def test_sweep_is_off_at_zero(manager, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "index_idle_unload_seconds", 0)
    assert manager.unload_idle_indexes() == 0
    assert manager.index_memory_stats()["loaded"] == 2
