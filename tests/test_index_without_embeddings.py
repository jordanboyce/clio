"""A locked-down network may never reach a model hub or provider.

Sources must still index and be found by keyword, and semantic search must
catch up by itself once a model is there.
"""

import json
import time

import numpy as np
import pytest

from models.schemas import ChunkMetadata, SearchMode
from services.chunker import TextChunker
from services.document_extractor import DocumentExtractor
from services.embedder import EmbeddingDimensionMismatch, EmbeddingUnavailable
from services.indexing.indexer import DocumentIndexer
from services.vector_store import VectorStore

DIM = 8


class Embedder:
    """Fails like a blocked hub until `available` is set."""

    model_name = "test-model"
    embedding_dim = DIM

    def __init__(self, available=False):
        self.available = available

    def _check(self):
        if not self.available:
            raise EmbeddingUnavailable("huggingface.co is blocked")

    def embed_texts(self, texts, progress_callback=None):
        self._check()
        return np.asarray([self._vector(t) for t in texts], dtype=np.float32)

    def embed_query(self, query):
        self._check()
        return self._vector(query)

    @staticmethod
    def _vector(text):
        vector = np.ones(DIM, dtype=np.float32)
        vector[hash(text) % DIM] += 1
        return vector


@pytest.fixture()
def env(tmp_path):
    store = VectorStore(tmp_path / "indexes", embedding_dim=DIM)
    embedder = Embedder()
    indexer = DocumentIndexer(store, embedder, DocumentExtractor(enable_ocr=False), TextChunker())
    return indexer, embedder, store, tmp_path


def _upload(indexer, tmp_path, name="notes.txt", text="Groundwater recharge depends on episodic flooding."):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return indexer.index_document(path, name)


def _wait_for_vectors(indexer, store, expected):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if store.index.ntotal == expected and not store.vectors_pending and not indexer._fill_running:
            return
        time.sleep(0.01)
    raise AssertionError(f"{store.index.ntotal} vectors, expected {expected}")


def test_a_source_indexes_and_is_found_with_no_embedding_model(env):
    indexer, _, store, tmp_path = env

    document = _upload(indexer, tmp_path)

    assert document.total_chunks == 1
    assert store.index.ntotal == 0 and store.vectors_pending

    found = indexer.search("groundwater recharge")
    assert [r.filename for r in found["results"]] == ["notes.txt"]
    assert found["retrieval"]["mode"] == "keyword"


def test_filtered_search_reaches_chunks_that_have_no_vectors(env):
    indexer, _, store, tmp_path = env
    _upload(indexer, tmp_path)
    _upload(indexer, tmp_path, "other.txt", "Basalt aquifers store groundwater for decades.")
    other = {c["chunk_id"] for c in store.metadata_store.get_all_chunks_ordered() if c["filename"] == "other.txt"}

    assert len(indexer.search("groundwater", mode=SearchMode.KEYWORD)["results"]) == 2

    found = store.search_hybrid("groundwater", Embedder._vector("groundwater"), top_k=3, allowed_chunk_ids=other)
    assert [r.filename for r in found] == ["other.txt"]


def test_vectors_catch_up_once_a_model_is_there(env):
    indexer, embedder, store, tmp_path = env
    _upload(indexer, tmp_path)
    _upload(indexer, tmp_path, "other.txt", "Basalt aquifers store groundwater for decades.")

    embedder.available = True
    # Keyword hits are served even before the catch-up has finished.
    found = indexer.search("groundwater recharge")
    assert "notes.txt" in {r.filename for r in found["results"]}

    _wait_for_vectors(indexer, store, 2)
    assert store.index_path.exists()

    # Pending is worked out again when the index is read back from disk.
    store.load()
    assert store.index.ntotal == 2 and not store.vectors_pending


def test_a_restart_remembers_chunks_still_waiting_for_vectors(tmp_path):
    indexes = tmp_path / "indexes"
    store = VectorStore(indexes, embedding_dim=DIM)
    indexer = DocumentIndexer(store, Embedder(), DocumentExtractor(enable_ocr=False), TextChunker())
    _upload(indexer, tmp_path)
    indexer.save_index()

    reopened = VectorStore(indexes, embedding_dim=DIM)
    assert reopened.vectors_pending
    assert reopened.get_total_chunks() == 1

    embedder = Embedder(available=True)
    indexer = DocumentIndexer(reopened, embedder, DocumentExtractor(enable_ocr=False), TextChunker())
    assert indexer.fill_missing_vectors() == 1
    assert reopened.index.ntotal == 1 and not reopened.vectors_pending


def test_a_failed_catch_up_leaves_keyword_search_working_and_waits(env):
    indexer, embedder, store, tmp_path = env
    _upload(indexer, tmp_path)

    embedder.available = True
    real = embedder.embed_texts

    def flaky(texts, progress_callback=None):
        raise EmbeddingUnavailable("model server went away")

    embedder.embed_texts = flaky
    indexer._run_vector_fill()
    assert store.vectors_pending and store.index.ntotal == 0
    assert indexer._fill_not_before > time.monotonic()

    # Inside the wait, a new search does not start another attempt.
    embedder.embed_texts = real
    indexer._catch_up_vectors()
    assert not indexer._fill_running and store.index.ntotal == 0

    indexer._fill_not_before = 0.0
    indexer._catch_up_vectors()
    _wait_for_vectors(indexer, store, 1)


def test_a_chunk_deleted_before_its_vector_arrives_stays_gone(env):
    indexer, embedder, store, tmp_path = env
    document = _upload(indexer, tmp_path)
    row_ids = store.chunks_without_vectors()
    vectors = Embedder(available=True).embed_texts(["text"])

    indexer.delete_document(document.document_id)
    store.add_vectors(row_ids, vectors)

    assert store.index.ntotal == 0 and store.chunks_without_vectors() == []


def test_deleting_a_keyword_only_source_removes_it(env):
    indexer, _, store, tmp_path = env
    document = _upload(indexer, tmp_path)

    indexer.delete_document(document.document_id)

    assert store.get_total_chunks() == 0
    assert indexer.search("groundwater")["results"] == []


def test_a_bulk_job_indexes_when_the_model_is_out_of_reach(env, monkeypatch):
    # Links, folders and the browser's uploads run through this job. It used to
    # refuse up front whenever the model was unavailable.
    import services.upload_service as us
    from tests.test_link_indexing import _fetched

    indexer, _, store, tmp_path = env
    monkeypatch.setattr(us, "fetch_link", lambda url: _fetched(url))
    monkeypatch.setattr(us.indexer_manager, "get_indexer", lambda cid: indexer)
    monkeypatch.setattr(us.indexer_manager, "get_documents_path", lambda cid: tmp_path / "docs")
    monkeypatch.setattr(us.collection_service, "add_document", lambda cid, did: None)
    updates = []
    monkeypatch.setattr(us.app_db, "update_upload_job", lambda job_id, **kw: updates.append(kw))

    service = us.UploadService()
    service._cancel_flags[11] = False
    service._run_link_index(11, ["https://example.com/a"], "c1")

    done = [u for u in updates if u.get("status") in ("completed", "failed")][-1]
    assert done["status"] == "completed", done
    assert json.loads(done["result_summary"])["documents_processed"] == 1
    assert store.get_total_chunks() > 0 and store.index.ntotal == 0 and store.vectors_pending


def test_a_model_that_does_not_fit_the_index_still_refuses(env):
    indexer, embedder, _, tmp_path = env

    def mismatch(texts, progress_callback=None):
        raise EmbeddingDimensionMismatch("other-model", 768, DIM)

    embedder.embed_texts = mismatch

    with pytest.raises(EmbeddingDimensionMismatch):
        _upload(indexer, tmp_path)


def test_a_store_with_every_vector_never_counts_chunks(env, monkeypatch):
    # Search must cost exactly what it did for a collection that is in sync.
    indexer, embedder, store, tmp_path = env
    embedder.available = True
    _upload(indexer, tmp_path)
    assert not store.vectors_pending

    monkeypatch.setattr(store.metadata_store, "get_total_chunks",
                        lambda: pytest.fail("counted chunks on an in-sync search"))

    assert store.searchable_chunks() == 1
    assert store.search_hybrid("groundwater", Embedder._vector("groundwater"), top_k=3)


def test_add_chunks_takes_no_vectors(tmp_path):
    store = VectorStore(tmp_path / "indexes", embedding_dim=DIM)
    chunk = ChunkMetadata(chunk_id="d_p1_c0", document_id="d", filename="f.txt",
                          page_number=1, chunk_index=0, text="groundwater")

    store.add_chunks([chunk], None)

    assert store.index.ntotal == 0 and store.vectors_pending
    assert store.bm25_index.search("groundwater", 5)
