"""Deleting many sources as one background job.

What a person experiences is covered from the outside: the request returns at
once, the job reports how far it got, sources added back while it runs survive
it, and stopping it leaves whole documents behind. The cost that made this a
job in the first place is pinned by counting the index saves and vector
removals, since a timing assertion would only measure the test machine.
"""

import json
import os
import threading
import time
from pathlib import Path

import faiss
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import documents as documents_api
from models.schemas import ChunkMetadata
from services import governance, removal_jobs
from services.app_database import app_db, SQLiteBackend
from services.bm25_service import BM25Index
from services.indexer_manager import indexer_manager
from services.upload_service import UploadService, upload_service
from services.vector_store import VectorStore

DIM = 8


def _wait_for(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _add_docs(store: VectorStore, documents_dir: Path, count: int, chunks_each: int = 3, start: int = 0,
              **doc_fields):
    rng = np.random.default_rng(start + 1)
    ids = []
    for n in range(start, start + count):
        doc_id = f"doc{n:04d}"
        chunks = [
            ChunkMetadata(chunk_id=f"{doc_id}_{c}", document_id=doc_id, filename=f"{doc_id}.txt",
                          page_number=c + 1, chunk_index=c, text=f"alpha bravo word{n}x{c}")
            for c in range(chunks_each)
        ]
        store.add_chunks(chunks, rng.random((chunks_each, DIM), dtype=np.float32))
        store.metadata_store.add_document(
            document_id=doc_id, filename=f"{doc_id}.txt", num_pages=1, num_chunks=chunks_each,
            upload_timestamp="2026-01-01T00:00:00", source_format="txt", extraction_method="text",
            embedding_model="test", chunk_size=100, chunk_overlap=0, **doc_fields,
        )
        (documents_dir / f"{doc_id}.txt").write_text(f"contents of {doc_id}", encoding="utf-8")
        ids.append(doc_id)
    store.save()
    return ids


@pytest.fixture
def collection(tmp_path, monkeypatch):
    """A real store and documents folder behind the collection's indexer."""
    old_db_path = app_db.db_path
    app_db.db_path = tmp_path / "app.db"
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    # A new database starts its job ids again.
    removal_jobs._pending.clear()

    documents_dir = tmp_path / "documents"
    documents_dir.mkdir()
    store = VectorStore(tmp_path / "indexes", embedding_dim=DIM)

    class FakeIndexer:
        vector_store = store
        figures_dir = tmp_path / "figures"
        delete_documents = None

        def save_index(self):
            store.save()

    indexer = FakeIndexer()
    from services.indexing.indexer import DocumentIndexer
    indexer.delete_documents = lambda ids: DocumentIndexer.delete_documents(indexer, ids)
    indexer.count_documents = lambda **_: len(store.metadata_store.list_document_ids())

    monkeypatch.setattr(indexer_manager, "get_indexer", lambda cid="default": indexer)
    monkeypatch.setattr(indexer_manager, "get_documents_path", lambda cid="default": documents_dir)
    monkeypatch.setattr(documents_api, "get_indexer", lambda cid="default": indexer)
    monkeypatch.setattr(documents_api, "_require_write", lambda cid: None)

    yield type("Collection", (), {"id": "c1", "store": store, "dir": documents_dir, "indexer": indexer,
                                  "root": tmp_path})()
    app_db.db_path = old_db_path


@pytest.fixture
def api(collection):
    app = FastAPI()
    app.include_router(documents_api.router)
    return TestClient(app)


def _finished(job_id):
    assert _wait_for(lambda: not upload_service.is_live(job_id)), "job did not finish"
    return upload_service.get_job_status(job_id)


def _bm25_count(store: VectorStore) -> int:
    return store.bm25_index.get_stats()["num_documents"]


# ── Deleting some sources ───────────────────────────────────────────────


def test_a_delete_job_removes_documents_from_every_store(collection):
    ids = _add_docs(collection.store, collection.dir, 5)

    job = removal_jobs.submit("c1", ids[:2], actor="ann")
    status = _finished(job)

    assert status["status"] == "completed"
    assert status["job_type"] == "delete"
    assert status["processed_files"] == 2
    assert status["result_summary"]["documents_processed"] == 2
    assert status["result_summary"]["total_chunks"] == 6

    assert sorted(collection.store.metadata_store.list_document_ids()) == ids[2:]
    assert collection.store.index.ntotal == 9
    assert _bm25_count(collection.store) == 9
    assert not (collection.dir / f"{ids[0]}.txt").exists()
    assert (collection.dir / f"{ids[2]}.txt").exists()


def test_each_document_still_gets_its_own_audit_row(collection):
    ids = _add_docs(collection.store, collection.dir, 3)

    _finished(removal_jobs.submit("c1", ids[:2], actor="ann"))

    rows = app_db.list_audit_events(action="document.delete")
    assert sorted(r["document_id"] for r in rows) == ids[:2]
    assert {r["actor"] for r in rows} == {"ann"}
    assert json.loads(rows[0]["detail"])["chunks_deleted"] == 3


def test_the_expensive_steps_happen_per_batch_and_per_job_not_per_document(collection, monkeypatch):
    """The whole point. One document used to mean one full index rewrite."""
    ids = _add_docs(collection.store, collection.dir, 450, chunks_each=1)
    monkeypatch.setattr(removal_jobs, "BATCH_DOCUMENTS", 200)

    saves = []
    real_save = collection.store.save
    monkeypatch.setattr(collection.store, "save", lambda: (saves.append(1), real_save()))
    removals = []
    real_index = collection.store.index

    class CountingIndex:
        def remove_ids(self, ids_):
            removals.append(len(ids_))
            return real_index.remove_ids(ids_)

        def __getattr__(self, name):
            return getattr(real_index, name)

    collection.store.index = CountingIndex()
    monkeypatch.setattr(collection.store, "_write_index_atomic", lambda index: None)

    _finished(removal_jobs.submit("c1", ids[:440], actor=None))

    assert len(saves) == 1
    assert removals == [200, 200, 40]


def test_a_big_document_gets_a_batch_of_its_own(monkeypatch):
    monkeypatch.setattr(removal_jobs, "BATCH_DOCUMENTS", 100)
    monkeypatch.setattr(removal_jobs, "BATCH_CHUNKS", 1000)
    docs = [{"document_id": "a", "num_chunks": 900}, {"document_id": "b", "num_chunks": 900},
            {"document_id": "c", "num_chunks": None}, {"document_id": "d", "num_chunks": 5000},
            {"document_id": "e", "num_chunks": 1}]

    batches = [[d["document_id"] for d in b] for b in removal_jobs._batches(docs)]

    assert batches == [["a"], ["b", "c"], ["d"], ["e"]]


def test_unknown_ids_are_counted_not_fatal(collection):
    ids = _add_docs(collection.store, collection.dir, 3)

    status = _finished(removal_jobs.submit("c1", [ids[0], "no-such-doc"], actor=None))

    assert status["status"] == "completed"
    assert status["result_summary"]["documents_processed"] == 1
    assert status["result_summary"]["not_found"] == 1


def test_a_document_marked_replaced_is_current_again_when_its_replacement_goes(collection):
    ids = _add_docs(collection.store, collection.dir, 3)
    collection.store.metadata_store.set_document_superseded(ids[0], ids[1])

    _finished(removal_jobs.submit("c1", [ids[1]], actor=None))

    assert collection.store.metadata_store.get_document_info(ids[0])["superseded_by"] is None


def test_a_file_that_cannot_be_removed_is_reported_and_the_rest_carry_on(collection, monkeypatch):
    ids = _add_docs(collection.store, collection.dir, 3)
    real = governance.remove_source_file

    def locked(documents_dir, doc, written_before=None):
        if doc["document_id"] == ids[0]:
            raise PermissionError("file is open elsewhere")
        return real(documents_dir, doc, written_before=written_before)

    monkeypatch.setattr(governance, "remove_source_file", locked)

    status = _finished(removal_jobs.submit("c1", ids[:2], actor=None))

    assert status["status"] == "completed"
    assert status["result_summary"]["failed_files"][0]["filename"] == f"{ids[0]}.txt"
    assert not (collection.dir / f"{ids[1]}.txt").exists()


# ── Stopping ────────────────────────────────────────────────────────────


def test_cancelling_stops_at_a_batch_and_keeps_the_rest(collection, monkeypatch):
    ids = _add_docs(collection.store, collection.dir, 6, chunks_each=1)
    monkeypatch.setattr(removal_jobs, "BATCH_DOCUMENTS", 2)

    first_batch_done = threading.Event()
    release = threading.Event()
    real = governance.remove_documents

    def gated(*args, **kwargs):
        outcome = real(*args, **kwargs)
        first_batch_done.set()
        release.wait(5)
        return outcome

    monkeypatch.setattr(governance, "remove_documents", gated)

    # Five of six, so this takes the batched path rather than the clear.
    job = removal_jobs.submit("c1", ids[:5], actor=None)
    assert first_batch_done.wait(5)
    assert upload_service.cancel_job(job) == "cancelling"
    release.set()
    status = _finished(job)

    assert status["status"] == "cancelled"
    assert status["processed_files"] == 2
    assert "Stopped after deleting 2 of 5" in status["error"]
    assert sorted(collection.store.metadata_store.list_document_ids()) == ids[2:]
    # What was removed is saved: a restart must not bring those vectors back.
    assert VectorStore(collection.root / "indexes", embedding_dim=DIM).index.ntotal == 4


def test_a_job_cancelled_while_queued_stops_counting_as_pending(collection):
    ids = _add_docs(collection.store, collection.dir, 3)
    release = threading.Event()
    # Holds the collection, so the delete job has to wait behind it.
    blocker = upload_service.submit_job("c1", "index", 1, lambda job_id: release.wait(10))
    assert _wait_for(lambda: upload_service.queue_position(blocker) is None)

    job = removal_jobs.submit("c1", ids[:1], actor=None)
    assert removal_jobs.is_removing("c1")
    assert upload_service.cancel_job(job) == "cancelled"
    assert not removal_jobs.is_removing("c1")

    release.set()
    _finished(blocker)
    assert ids[0] in collection.store.metadata_store.list_document_ids()


# ── Clearing a collection ───────────────────────────────────────────────


def test_clearing_empties_everything_without_touching_documents_one_by_one(collection, monkeypatch):
    ids = _add_docs(collection.store, collection.dir, 5)
    collection.store.metadata_store.set_chunk_boost(f"{ids[0]}_0", 0.5, None)

    def per_document_path(*_args, **_kwargs):
        raise AssertionError("emptying a collection must not walk it document by document")

    monkeypatch.setattr(governance, "remove_documents", per_document_path)

    status = _finished(removal_jobs.submit("c1", None, actor="ann"))

    assert status["status"] == "completed"
    assert status["processed_files"] == 5
    assert collection.store.metadata_store.list_document_ids() == []
    assert collection.store.index.ntotal == 0
    assert _bm25_count(collection.store) == 0
    assert list(collection.dir.iterdir()) == []
    assert collection.store.metadata_store.get_chunk_boosts([f"{ids[0]}_0"]) == {}
    assert len(app_db.list_audit_events(action="document.delete")) == 5
    assert VectorStore(collection.root / "indexes", embedding_dim=DIM).index.ntotal == 0


def test_naming_every_document_takes_the_fast_path_too(collection, monkeypatch):
    ids = _add_docs(collection.store, collection.dir, 3)
    monkeypatch.setattr(governance, "remove_documents",
                        lambda *a, **k: pytest.fail("should have cleared the collection"))

    status = _finished(removal_jobs.submit("c1", ids, actor=None))

    assert status["status"] == "completed"
    assert collection.store.metadata_store.list_document_ids() == []


def test_clearing_leaves_files_it_does_not_own(collection):
    _add_docs(collection.store, collection.dir, 1)
    outside = collection.root / "elsewhere.txt"
    outside.write_text("mine", encoding="utf-8")
    collection.store.metadata_store.update_document_source(
        "doc0000", source_path=str(outside), source_type="local_reference")
    # Same name in the library as the reference, which must also not be deleted.
    (collection.dir / "doc0000.txt").write_text("library copy", encoding="utf-8")

    _finished(removal_jobs.submit("c1", None, actor=None))

    assert outside.exists()
    assert (collection.dir / "doc0000.txt").exists()


def test_clearing_includes_what_jobs_ahead_of_it_added(collection):
    _add_docs(collection.store, collection.dir, 2)
    release = threading.Event()

    def add_more(job_id):
        release.wait(10)
        _add_docs(collection.store, collection.dir, 3, start=100)

    blocker = upload_service.submit_job("c1", "index", 1, add_more)
    job = removal_jobs.submit("c1", None, actor=None)
    release.set()
    _finished(blocker)
    status = _finished(job)

    assert status["processed_files"] == 5
    assert collection.store.metadata_store.list_document_ids() == []


def test_emptying_the_keyword_index_keeps_it_usable(tmp_path):
    bm25 = BM25Index(tmp_path / "bm25.db")
    bm25.add_documents_batch([("a", "alpha bravo"), ("b", "bravo charlie")])

    bm25.clear()

    assert bm25.search("bravo") == []
    assert bm25.get_stats()["num_documents"] == 0
    assert bm25.get_stats()["vocabulary_size"] == 0
    bm25.add_documents_batch([("c", "bravo delta")])
    assert [cid for cid, _ in bm25.search("bravo")] == ["c"]


def test_a_failed_keyword_rebuild_keeps_the_old_index(tmp_path):
    bm25 = BM25Index(tmp_path / "bm25.db")
    bm25.add_documents_batch([("a", "alpha bravo")])

    def broken():
        yield ("b", "charlie")
        raise RuntimeError("chunk text unreadable")

    with pytest.raises(RuntimeError):
        bm25.rebuild(broken(), batch_size=1)

    assert [cid for cid, _ in bm25.search("alpha")] == ["a"]


# ── Adding back while the delete is still running ───────────────────────


def test_adding_a_file_back_is_not_skipped_because_its_delete_is_queued(collection, tmp_path):
    """Delete everything, add it back, in that order and without waiting."""
    source = tmp_path / "report.txt"
    source.write_text("the report", encoding="utf-8")
    content_hash = governance.content_hash_of(source)
    collection.store.metadata_store.add_document(
        document_id=content_hash[:16], filename="report.txt", num_pages=1, num_chunks=0,
        upload_timestamp="2026-01-01", source_format="txt", extraction_method="text",
        embedding_model="m", chunk_size=1, chunk_overlap=0, content_hash=content_hash,
    )

    to_index, unchanged, _ = UploadService._split_unchanged([str(source)], "c1")
    assert (to_index, unchanged) == ([], [str(source)])

    release = threading.Event()
    blocker = upload_service.submit_job("c1", "index", 1, lambda job_id: release.wait(10))
    job = removal_jobs.submit("c1", None, actor=None)

    to_index, unchanged, _ = UploadService._split_unchanged([str(source)], "c1")
    assert (to_index, unchanged) == ([str(source)], [])

    release.set()
    _finished(blocker)
    _finished(job)


def test_only_the_documents_being_deleted_count_as_gone(monkeypatch):
    monkeypatch.setattr(upload_service, "is_live", lambda job: True)
    monkeypatch.setattr(removal_jobs, "_pending", {1: ("c1", {"gone"}), 2: ("other", None)})
    existing = {"h1": "gone", "h2": "kept"}

    assert removal_jobs.without_pending_removals("c1", existing) == {"h2": "kept"}
    assert removal_jobs.without_pending_removals("elsewhere", existing) == existing
    assert removal_jobs.without_pending_removals("other", existing) == {}


def test_a_file_uploaded_after_the_request_is_left_for_the_job_adding_it(collection):
    ids = _add_docs(collection.store, collection.dir, 3)
    release = threading.Event()
    blocker = upload_service.submit_job("c1", "index", 1, lambda job_id: release.wait(10))

    job = removal_jobs.submit("c1", ids[:1], actor=None)
    # The same name, written again while the delete waits its turn.
    reuploaded = collection.dir / f"{ids[0]}.txt"
    time.sleep(0.05)
    reuploaded.write_text("the new upload", encoding="utf-8")
    os.utime(reuploaded, (time.time() + 5, time.time() + 5))

    release.set()
    _finished(blocker)
    _finished(job)

    assert reuploaded.read_text(encoding="utf-8") == "the new upload"
    assert ids[0] not in collection.store.metadata_store.list_document_ids()


# ── Crash recovery ──────────────────────────────────────────────────────


def test_vectors_left_by_an_interrupted_delete_are_dropped_on_open(collection):
    ids = _add_docs(collection.store, collection.dir, 3)
    # Rows committed, the process dies before the index is saved.
    collection.store.metadata_store.delete_documents(ids[:1])

    reopened = VectorStore(collection.root / "indexes", embedding_dim=DIM)

    assert reopened.index.ntotal == 6
    vector_ids = set(faiss.vector_to_array(faiss.downcast_index(reopened.index).id_map).tolist())
    assert vector_ids == set(reopened.metadata_store.get_all_rowids_ordered())


def test_a_healthy_index_is_left_alone_on_open(collection, monkeypatch):
    _add_docs(collection.store, collection.dir, 2)
    monkeypatch.setattr(collection.store.metadata_store, "get_all_rowids_ordered",
                        lambda: pytest.fail("no drift, no scan"))

    collection.store._drop_orphan_vectors()


# ── The dispatcher ──────────────────────────────────────────────────────


def _queued(service, job_id, collection_id, started, release, light=False):
    from services.upload_service import _QueuedJob

    def body():
        started.append(job_id)
        release.wait(5)

    service._submit(_QueuedJob(job_id=job_id, collection_id=collection_id, target=body, args=(), light=light))


def test_a_delete_does_not_wait_for_another_collections_indexing(monkeypatch):
    from services import upload_service as module
    monkeypatch.setattr(module.settings, "max_concurrent_index_jobs", 1)
    service = UploadService()
    started, release = [], threading.Event()

    _queued(service, 1, "busy", started, release)
    _queued(service, 2, "other", started, release)  # needs a slot: waits
    _queued(service, 3, "third", started, release, light=True)  # does not

    assert _wait_for(lambda: 3 in started)
    assert 2 not in started
    release.set()
    assert _wait_for(lambda: service.active_job_count() == 0 and service.queued_job_count() == 0)
    assert 2 in started


def test_a_delete_keeps_its_place_behind_a_job_for_the_same_collection(monkeypatch):
    from services import upload_service as module
    monkeypatch.setattr(module.settings, "max_concurrent_index_jobs", 1)
    service = UploadService()
    started, release = [], threading.Event()

    _queued(service, 1, "busy", started, release)
    _queued(service, 2, "other", started, release)  # waiting on a slot
    _queued(service, 3, "other", started, release, light=True)  # must stay behind job 2

    assert _wait_for(lambda: 1 in started)
    time.sleep(0.05)
    assert started == [1]
    release.set()
    assert _wait_for(lambda: service.active_job_count() == 0 and service.queued_job_count() == 0)
    assert started.index(2) < started.index(3)


# ── The endpoint ────────────────────────────────────────────────────────


def test_endpoint_starts_a_job_and_answers_at_once(api, collection):
    ids = _add_docs(collection.store, collection.dir, 3)

    response = api.post("/documents/bulk-delete", json={"collection_id": "c1", "document_ids": ids[:2]})

    assert response.status_code == 202
    body = response.json()
    assert body["job_type"] == "delete"
    assert body["total_files"] == 2
    assert _finished(body["job_id"])["status"] == "completed"


def test_endpoint_clears_on_request(api, collection):
    _add_docs(collection.store, collection.dir, 3)

    response = api.post("/documents/bulk-delete", json={"collection_id": "c1", "everything": True})

    assert response.status_code == 202
    assert response.json()["total_files"] == 3
    assert _finished(response.json()["job_id"])["processed_files"] == 3


@pytest.mark.parametrize("body", [
    {"collection_id": "c1"},
    {"collection_id": "c1", "everything": True, "document_ids": ["a"]},
])
def test_endpoint_wants_exactly_one_of_ids_or_everything(api, body):
    assert api.post("/documents/bulk-delete", json=body).status_code == 422


@pytest.mark.parametrize("body", [
    {"collection_id": "c1", "everything": True},
    {"collection_id": "c1", "document_ids": []},
])
def test_endpoint_says_when_there_was_nothing_to_do(api, body):
    response = api.post("/documents/bulk-delete", json=body)

    assert response.status_code == 200
    assert response.json()["job_id"] is None


def test_endpoint_refuses_while_the_collection_is_being_reindexed(api, collection, monkeypatch):
    _add_docs(collection.store, collection.dir, 1)
    job_id = app_db.create_reindex_job({"collection_id": "c1"})

    response = api.post("/documents/bulk-delete", json={"collection_id": "c1", "everything": True})

    assert response.status_code == 409
    assert collection.store.metadata_store.list_document_ids() == ["doc0000"]
    app_db.update_reindex_job(job_id, status="completed")


def test_endpoint_is_not_blocked_by_another_collections_reindex(api, collection):
    _add_docs(collection.store, collection.dir, 1)
    job_id = app_db.create_reindex_job({"collection_id": "somewhere-else"})

    response = api.post("/documents/bulk-delete", json={"collection_id": "c1", "everything": True})

    assert response.status_code == 202
    _finished(response.json()["job_id"])
    app_db.update_reindex_job(job_id, status="completed")


def test_endpoint_checks_write_access(api, collection, monkeypatch):
    from fastapi import HTTPException

    def refuse(cid):
        raise HTTPException(status_code=403, detail="read-only")

    monkeypatch.setattr(documents_api, "_require_write", refuse)
    _add_docs(collection.store, collection.dir, 1)

    response = api.post("/documents/bulk-delete", json={"collection_id": "c1", "everything": True})

    assert response.status_code == 403
    assert collection.store.metadata_store.list_document_ids() == ["doc0000"]


def test_a_finished_jobs_stream_ends_with_the_event_clients_listen_for(api, collection):
    _add_docs(collection.store, collection.dir, 1)
    response = api.post("/documents/bulk-delete", json={"collection_id": "c1", "everything": True})
    job_id = response.json()["job_id"]
    _finished(job_id)

    stream = api.get(f"/documents/upload/{job_id}/stream")

    assert "event: job_complete" in stream.text


def test_a_stream_for_an_unknown_job_leaves_no_subscriber_behind(api):
    assert api.get("/documents/upload/999999/stream").status_code == 404
    assert 999999 not in upload_service._event_queues


# ── Re-index around a delete ────────────────────────────────────────────


def test_a_reindex_is_refused_while_sources_are_being_deleted(collection):
    import asyncio

    from services.reindex_service import ReindexService

    _add_docs(collection.store, collection.dir, 2)
    release = threading.Event()
    blocker = upload_service.submit_job("c1", "index", 1, lambda job_id: release.wait(10))
    job = removal_jobs.submit("c1", None, actor=None)

    with pytest.raises(RuntimeError, match="being deleted"):
        asyncio.run(ReindexService().start_extension_reindex("c1", [".txt"]))

    release.set()
    _finished(blocker)
    _finished(job)


def test_reindex_reports_files_that_did_not_come_back(tmp_path, collection, monkeypatch):
    from services import reindex_service as module
    from services.reindex_service import ReindexService

    service = ReindexService()
    job_id = app_db.create_reindex_job({"collection_id": "c1"})

    good = tmp_path / "good.txt"
    good.write_text("fine", encoding="utf-8")
    bad = tmp_path / "bad.txt"
    bad.write_text("broken", encoding="utf-8")

    class Indexer:
        seen = []

        def index_document_with_progress(self, path, name, progress_callback=None):
            progress_callback("embedding", 50, "Embedded 1/2 chunks", 1, 2)
            Indexer.seen.append(service.live_progress(job_id))
            if name == "bad.txt":
                raise ValueError("could not read it")
            return type("Meta", (), {"document_id": "x", "total_pages": 1, "total_chunks": 1})()

    failures = service._reindex_documents(
        job_id, Indexer(), collection.store,
        [{"path": good}, {"path": bad}, {"path": tmp_path / "missing.txt"}],
    )
    service._finish_reindex(job_id, 3, failures)

    assert Indexer.seen[0]["phase"] == "embedding"
    assert Indexer.seen[0]["chunks_total"] == 2
    job = app_db.get_reindex_job(job_id)
    assert job["status"] == "failed"
    assert job["processed_documents"] == 1
    assert job["current_file"] is None
    assert "2 of 3" in job["error"] and "bad.txt" in job["error"] and "missing.txt" in job["error"]
    assert module.is_reindexing("c1") is False
