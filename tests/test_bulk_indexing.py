"""Tests for the bulk ingest path: chunk accumulation across files, batched
embedding, and batched SQLite/BM25/document persistence."""

import hashlib

import numpy as np
import pytest

from services.chunker import TextChunker
from services.document_extractor import DocumentExtractor
from services.indexing import ChunkBatcher, DocumentIndexer
from services.metadata_store import MetadataStore
from services.vector_store import VectorStore

EMBED_DIM = 8


class FakeEmbedder:
    """Deterministic embedding service that records every embed_texts call."""

    model_name = "fake-embed"
    embedding_dim = EMBED_DIM

    def __init__(self):
        self.calls = []

    @staticmethod
    def _vec(text: str) -> list:
        digest = hashlib.sha256(text.encode()).digest()
        return [b / 255.0 + 0.01 for b in digest[:EMBED_DIM]]

    def embed_texts(self, texts, progress_callback=None):
        self.calls.append(list(texts))
        if progress_callback:
            progress_callback(len(texts), len(texts))
        return np.array([self._vec(t) for t in texts], dtype=np.float32)

    def embed_query(self, query):
        return np.array(self._vec(query), dtype=np.float32)


@pytest.fixture
def embedder():
    return FakeEmbedder()


@pytest.fixture
def indexer(tmp_path, embedder):
    return DocumentIndexer(
        vector_store=VectorStore(index_dir=tmp_path / "indexes", embedding_dim=EMBED_DIM),
        embedding_service=embedder,
        document_extractor=DocumentExtractor(),
        text_chunker=TextChunker(chunk_size=200, chunk_overlap=40),
    )


_WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"]


def _write_txt(tmp_path, name, paragraphs=3):
    # Paragraphs are deliberately distinct: near-identical repetitive text can
    # make the chunker emit duplicate chunk texts, which would tie in search.
    path = tmp_path / name
    text = "\n\n".join(
        f"Section {i} of {name} discusses {_WORDS[i % 8]} subsystems in detail, "
        f"covering the {_WORDS[(i + 3) % 8]} pipeline and how {name} handles "
        f"scenario number {i * 17} during {_WORDS[(i + 5) % 8]} processing runs."
        for i in range(paragraphs)
    )
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# ChunkBatcher
# ---------------------------------------------------------------------------

def test_chunk_batcher_flushes_at_threshold():
    batcher = ChunkBatcher(flush_chunks=10)

    assert batcher.add("a", 4) is None
    assert batcher.add("b", 4) is None
    assert batcher.chunk_count == 8

    batch = batcher.add("c", 4)  # 12 >= 10 -> flush, document-aligned
    assert batch == ["a", "b", "c"]
    assert len(batcher) == 0
    assert batcher.chunk_count == 0


def test_chunk_batcher_drain_returns_remainder_in_order():
    batcher = ChunkBatcher(flush_chunks=100)
    for item in ("x", "y", "z"):
        assert batcher.add(item, 1) is None
    assert batcher.drain() == ["x", "y", "z"]
    assert batcher.drain() == []


def test_chunk_batcher_single_large_document_flushes_alone():
    batcher = ChunkBatcher(flush_chunks=10)
    assert batcher.add("big", 50) == ["big"]


# ---------------------------------------------------------------------------
# prepare_document
# ---------------------------------------------------------------------------

def test_prepare_document_extracts_without_persisting(tmp_path, indexer, embedder):
    path = _write_txt(tmp_path, "doc.txt")
    prepared = indexer.prepare_document(path, path.name)

    assert prepared is not None
    assert prepared.filename == "doc.txt"
    assert len(prepared.chunks) > 0
    assert prepared.total_pages == 1
    assert prepared.source_format == "txt"
    # Nothing embedded, nothing written
    assert embedder.calls == []
    assert indexer.vector_store.get_total_chunks() == 0
    assert indexer.list_documents() == []


def test_prepare_document_tabular_returns_none(tmp_path, indexer):
    path = tmp_path / "data.csv"
    path.write_text("name,value\nalpha,1\nbeta,2\n", encoding="utf-8")
    assert indexer.prepare_document(path, path.name) is None


def test_prepare_document_empty_file_raises(tmp_path, indexer):
    path = tmp_path / "empty.txt"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        indexer.prepare_document(path, path.name)


# ---------------------------------------------------------------------------
# index_prepared_documents
# ---------------------------------------------------------------------------

def test_index_prepared_documents_embeds_batch_in_one_call(tmp_path, indexer, embedder):
    prepared = [
        indexer.prepare_document(p, p.name)
        for p in (_write_txt(tmp_path, f"doc{i}.txt") for i in range(4))
    ]
    total_chunks = sum(len(p.chunks) for p in prepared)
    assert total_chunks > 4  # multiple chunks per file

    metas = indexer.index_prepared_documents(prepared)

    # All chunks across all files went through a single embed_texts call
    assert len(embedder.calls) == 1
    assert len(embedder.calls[0]) == total_chunks

    # Per-document metadata is intact and ordered
    assert [m.filename for m in metas] == [f"doc{i}.txt" for i in range(4)]
    assert all(m.total_chunks == len(p.chunks) for m, p in zip(metas, prepared))

    # Persistence: documents, chunks, and BM25 all landed
    docs = {d["filename"]: d for d in indexer.list_documents()}
    assert set(docs) == {f"doc{i}.txt" for i in range(4)}
    for meta in metas:
        assert docs[meta.filename]["num_chunks"] == meta.total_chunks
    assert indexer.vector_store.get_total_chunks() == total_chunks
    assert indexer.vector_store.index.ntotal == total_chunks
    assert indexer.vector_store.get_bm25_stats()["num_documents"] == total_chunks


def test_index_prepared_documents_search_finds_bulk_indexed_chunks(tmp_path, indexer):
    prepared = [
        indexer.prepare_document(p, p.name)
        for p in (_write_txt(tmp_path, f"doc{i}.txt") for i in range(3))
    ]
    indexer.index_prepared_documents(prepared)

    # The fake embedder is deterministic per text, so querying with an indexed
    # chunk's exact text must return that chunk first.
    target = prepared[1].chunks[0]
    results = indexer.vector_store.search(
        indexer.embedding_service.embed_query(target.text), top_k=3
    )
    assert results
    assert results[0].text_snippet == target.text
    assert results[0].document_id == target.document_id
    assert results[0].filename == "doc1.txt"


def test_index_prepared_documents_reports_progress(tmp_path, indexer):
    prepared = [
        indexer.prepare_document(p, p.name)
        for p in (_write_txt(tmp_path, f"doc{i}.txt") for i in range(2))
    ]
    total_chunks = sum(len(p.chunks) for p in prepared)

    seen = []
    indexer.index_prepared_documents(prepared, progress_callback=lambda d, t: seen.append((d, t)))
    assert seen[-1] == (total_chunks, total_chunks)


def test_index_prepared_documents_empty_batch_is_noop(indexer, embedder):
    assert indexer.index_prepared_documents([]) == []
    assert embedder.calls == []


def test_bulk_path_matches_single_file_path(tmp_path, embedder):
    """The batched path must produce the same documents/chunks as the
    one-file-at-a-time path it replaces (throughput regression guard)."""
    source = _write_txt(tmp_path, "same.txt", paragraphs=5)

    def build(subdir):
        return DocumentIndexer(
            vector_store=VectorStore(index_dir=tmp_path / subdir, embedding_dim=EMBED_DIM),
            embedding_service=FakeEmbedder(),
            document_extractor=DocumentExtractor(),
            text_chunker=TextChunker(chunk_size=200, chunk_overlap=40),
        )

    single = build("single")
    single_meta = single.index_document(source, source.name)

    bulk = build("bulk")
    bulk_meta = bulk.index_prepared_documents([bulk.prepare_document(source, source.name)])[0]

    assert bulk_meta.document_id == single_meta.document_id
    assert bulk_meta.total_pages == single_meta.total_pages
    assert bulk_meta.total_chunks == single_meta.total_chunks
    assert bulk.vector_store.get_total_chunks() == single.vector_store.get_total_chunks()

    doc_id = single_meta.document_id
    single_doc = single.vector_store.metadata_store.get_documents_info([doc_id])[doc_id]
    bulk_doc = bulk.vector_store.metadata_store.get_documents_info([doc_id])[doc_id]
    for key in ("filename", "num_pages", "num_chunks", "source_format",
                "extraction_method", "embedding_model", "chunk_size", "chunk_overlap"):
        assert bulk_doc[key] == single_doc[key]


def test_index_prepared_documents_is_idempotent_per_file(tmp_path, indexer):
    """Re-indexing the same prepared document (the per-file fallback after a
    failed batch) must not duplicate chunks, vectors, or document rows."""
    source = _write_txt(tmp_path, "retry.txt")
    prepared = indexer.prepare_document(source, source.name)

    indexer.index_prepared_documents([prepared])
    first_chunks = indexer.vector_store.get_total_chunks()
    indexer.index_prepared_documents([prepared])

    assert indexer.vector_store.get_total_chunks() == first_chunks
    assert indexer.vector_store.index.ntotal == first_chunks
    assert len(indexer.list_documents()) == 1


# ---------------------------------------------------------------------------
# MetadataStore.add_documents
# ---------------------------------------------------------------------------

def test_metadata_store_add_documents_batch_roundtrip(tmp_path):
    store = MetadataStore(tmp_path / "metadata.db")
    store.add_documents([
        {
            "document_id": f"doc-{i}",
            "filename": f"file{i}.txt",
            "num_pages": i + 1,
            "num_chunks": (i + 1) * 2,
            "upload_timestamp": "2026-08-25T00:00:00",
            "source_format": "txt",
            "embedding_model": "fake-embed",
            "chunk_size": 200,
            "chunk_overlap": 40,
            "injection_warnings": {"1": {"is_flagged": True}} if i == 0 else None,
        }
        for i in range(3)
    ])

    docs = store.get_documents_info(["doc-0", "doc-1", "doc-2"])
    assert set(docs) == {"doc-0", "doc-1", "doc-2"}
    assert docs["doc-1"]["num_pages"] == 2
    assert docs["doc-1"]["num_chunks"] == 4
    assert docs["doc-2"]["source_format"] == "txt"
    assert docs["doc-0"]["embedding_model"] == "fake-embed"


def test_metadata_store_add_documents_empty_is_noop(tmp_path):
    store = MetadataStore(tmp_path / "metadata.db")
    store.add_documents([])
    assert store.list_documents() == []


# ---------------------------------------------------------------------------
# UploadService bulk job pipeline (extraction pool + batched flushes)
# ---------------------------------------------------------------------------

def test_process_bulk_job_end_to_end(tmp_path, indexer, embedder, monkeypatch):
    """Drive the full bulk pipeline: files are indexed, per-file progress
    events still fire in order, one file failing doesn't fail the job, and
    the job row reaches completed with an accurate summary."""
    import json

    import services.upload_service as us

    n_ok = 6
    paths = [str(_write_txt(tmp_path, f"doc{i}.txt", paragraphs=2)) for i in range(n_ok)]
    bad = tmp_path / "bad.unsupported"
    bad.write_text("nope", encoding="utf-8")
    paths.insert(3, str(bad))

    job_updates = []
    collection_adds = []
    monkeypatch.setattr(
        us.app_db, "update_upload_job",
        lambda job_id, **kw: job_updates.append((job_id, kw)),
    )
    monkeypatch.setattr(
        us.collection_service, "add_document",
        lambda cid, doc_id: collection_adds.append((cid, doc_id)),
    )
    monkeypatch.setattr(
        us.indexer_manager, "get_indexer", lambda collection_id: indexer
    )
    # Force multiple flushes so the accumulate/flush boundary is exercised
    monkeypatch.setattr(us.settings, "bulk_flush_chunks", 4)

    service = us.UploadService()
    events = service.subscribe_to_events(job_id=42)
    service._cancel_flags[42] = False
    service._run_local_index(42, paths, "test-collection", copy_to_library=False)

    collected = []
    while not events.empty():
        collected.append(events.get_nowait())

    # Every file got its start event, in submission order
    starts = [e for e in collected if e.event_type == "file_start"]
    assert [e.file_index for e in starts] == list(range(1, n_ok + 2))

    completes = [e for e in collected if e.event_type == "file_complete"]
    assert len(completes) == n_ok
    assert all(e.chunks_total > 0 for e in completes)

    errors = [e for e in collected if e.event_type == "file_error"]
    assert len(errors) == 1
    assert errors[0].current_file == "bad.unsupported"

    assert [e.event_type for e in collected if e.event_type.startswith("job_")] == ["job_complete"]

    # All chunks were embedded across few large calls, not one call per file
    total_chunks = sum(len(c) for c in embedder.calls)
    assert indexer.vector_store.get_total_chunks() == total_chunks
    assert len(embedder.calls) < n_ok

    # Documents persisted, linked to the collection, with source recorded
    assert len(indexer.list_documents()) == n_ok
    assert len(collection_adds) == n_ok
    doc_ids = [d["document_id"] for d in indexer.list_documents()]
    infos = indexer.vector_store.metadata_store.get_documents_info(doc_ids)
    assert all(info["source_type"] == "local_reference" for info in infos.values())
    assert all(info["source_path"] for info in infos.values())

    # Job row reached completed with a full result summary
    final = job_updates[-1][1]
    assert final["status"] == "completed"
    assert final["processed_files"] == n_ok + 1
    summary = json.loads(final["result_summary"])
    assert summary["documents_processed"] == n_ok
    assert len(summary["failed_files"]) == 1
    assert summary["total_chunks"] == indexer.vector_store.get_total_chunks()


def test_cancel_after_extraction_finishes_still_cancels(tmp_path, indexer, monkeypatch):
    """Cancelling once every file is extracted must not finish as completed.

    Extraction runs ahead of embedding, so on a batch of small files the
    file loop can drain before the person clicks Cancel. The remaining work
    is one big embed, and the job used to sail past every cancellation
    check and report success.
    """
    import json

    import services.upload_service as us

    paths = [str(_write_txt(tmp_path, f"doc{i}.txt")) for i in range(4)]

    job_updates = []
    monkeypatch.setattr(
        us.app_db, "update_upload_job",
        lambda job_id, **kw: job_updates.append((job_id, kw)),
    )
    monkeypatch.setattr(us.collection_service, "add_document", lambda cid, doc_id: None)
    monkeypatch.setattr(us.indexer_manager, "get_indexer", lambda collection_id: indexer)
    # Nothing flushes until the final drain, so all the embedding work is
    # left for after the file loop has emptied.
    monkeypatch.setattr(us.settings, "bulk_flush_chunks", 100000)

    service = us.UploadService()
    service._cancel_flags[11] = False

    real_prepare = indexer.prepare_document

    def prepare_then_cancel(path, filename, **kwargs):
        prepared = real_prepare(path, filename, **kwargs)
        if filename == "doc3.txt":  # the last file: the loop is about to end
            service._cancel_flags[11] = True
        return prepared

    monkeypatch.setattr(indexer, "prepare_document", prepare_then_cancel)

    service._run_local_index(11, paths, "test-collection", copy_to_library=False)

    final = job_updates[-1][1]
    assert final["status"] == "cancelled"
    assert "Cancelled after indexing" in final["error"]
    # Nothing was embedded, so nothing landed in the index.
    assert indexer.list_documents() == []
    assert json.loads(final["result_summary"])["documents_processed"] == 0


def test_process_bulk_job_cancellation(tmp_path, indexer, monkeypatch):
    """Cancelling mid-job marks the job cancelled and stops processing."""
    import services.upload_service as us

    paths = [str(_write_txt(tmp_path, f"doc{i}.txt")) for i in range(5)]

    job_updates = []
    monkeypatch.setattr(
        us.app_db, "update_upload_job",
        lambda job_id, **kw: job_updates.append((job_id, kw)),
    )
    monkeypatch.setattr(us.collection_service, "add_document", lambda cid, doc_id: None)
    monkeypatch.setattr(us.indexer_manager, "get_indexer", lambda collection_id: indexer)

    service = us.UploadService()
    service._cancel_flags[7] = True  # cancelled before the first file
    service._run_local_index(7, paths, "test-collection", copy_to_library=False)

    assert job_updates[-1][1]["status"] == "cancelled"
    assert indexer.list_documents() == []


def test_a_failed_staged_upload_is_kept_for_its_retry(tmp_path, indexer, monkeypatch):
    """U2: the Retry a failed browser upload offers must find the file.

    The job used to delete every staged upload when it ended, failed ones
    too, so the Retry it had just offered always answered "gone". A file that
    was indexed moves into the library; one that failed stays in staging,
    where the stale-upload sweep removes it if nobody retries.
    """
    import json

    import services.upload_service as us

    staging = tmp_path / ".staged"
    library = tmp_path / "library"
    library.mkdir()
    good_dir, bad_dir = staging / "tmpgood", staging / "tmpbad"
    good_dir.mkdir(parents=True)
    bad_dir.mkdir()
    good = _write_txt(good_dir, "notes.txt")
    bad = bad_dir / "scan.unsupported"
    bad.write_text("nope", encoding="utf-8")

    job_updates = []
    monkeypatch.setattr(us.app_db, "update_upload_job", lambda job_id, **kw: job_updates.append(kw))
    monkeypatch.setattr(us.collection_service, "add_document", lambda cid, doc_id: None)
    monkeypatch.setattr(us.collection_service, "get_staging_path", lambda cid: staging)
    monkeypatch.setattr(us.indexer_manager, "get_indexer", lambda collection_id: indexer)
    monkeypatch.setattr(us.indexer_manager, "get_documents_path", lambda collection_id: library)

    service = us.UploadService()
    service._cancel_flags[5] = False
    service._run_local_index(5, [str(good), str(bad)], "test-collection", copy_to_library=False)

    failed = json.loads(job_updates[-1]["result_summary"])["failed_files"]
    assert [f["retry"] for f in failed] == ["tmpbad/scan.unsupported"]
    assert bad.is_file(), "the failed upload is kept for its Retry"
    assert not good_dir.exists(), "the indexed upload has moved out of staging"
