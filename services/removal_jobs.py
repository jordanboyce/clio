"""Deleting many sources as one background job.

Deleting used to be one HTTP request per source, each of which removed its
vectors (a walk of the whole index) and then rewrote the whole index file.
At 200,000 vectors that is about 350 ms per source on a fast disk, and the
browser sat in a modal until the last one came back, so a large collection
took half an hour, and closing the tab left it half deleted.

The work here is the same but its cost is paid once per batch, or once per
job for saving the index. Emptying a collection skips the per-document work
altogether. It runs on the upload dispatcher, so it appears in the jobs drawer,
keeps to the one-job-per-collection rule and survives the tab that started it.

Because the delete now finishes after the request does, two things that used
to be impossible have to be handled, and a person deleting everything and
adding it back will hit both within seconds:

* adding a source compares its hash with what is indexed, and must not treat
  a document waiting to be deleted as already there (``without_pending_removals``);
* a re-upload is written to the same path the job is about to delete, so the
  job leaves alone any file newer than the request (``requested_at``).
"""

import logging
import threading
import time
from typing import Any, Dict, Iterator, List, Optional

from services import audit, governance
from services.app_database import app_db
from services.collection_locks import collection_write_lock
from services.collection_service import collection_service
from services.indexer_manager import indexer_manager
from services.upload_service import upload_service

logger = logging.getLogger(__name__)

JOB_TYPE = "delete"
PHASE = "deleting"

# One batch is one remove_ids walk and one transaction per store. Smaller gives
# finer progress and earlier cancellation; larger is faster. Both limits apply
# so that a few enormous documents still report as they go.
BATCH_DOCUMENTS = 200
BATCH_CHUNKS = 5000
BATCH_FILES = 200

_lock = threading.Lock()
# job id -> (collection id, ids being removed, or None for all of them)
_pending: Dict[int, tuple] = {}


def submit(collection_id: str, document_ids: Optional[List[str]], *, actor: Optional[str]) -> int:
    """Queue the removal of these documents, or with ``None`` every document.

    Raises RuntimeError when the job queue is full.
    """
    ids = list(dict.fromkeys(document_ids)) if document_ids is not None else None
    if ids is None:
        total = len(indexer_manager.get_indexer(collection_id).vector_store.metadata_store.list_document_ids())
    else:
        total = len(ids)
    job_id = upload_service.submit_job(
        collection_id, JOB_TYPE, total, _run, args=(collection_id, ids, actor, time.time()), light=True,
    )
    with _lock:
        _pending[job_id] = (collection_id, set(ids) if ids is not None else None)
    audit.record("document.bulk_delete", actor=actor, collection_id=collection_id, target=str(job_id),
                 detail={"documents": total, "everything": ids is None})
    return job_id


def _live_removals(collection_id: str) -> List[Optional[set]]:
    """What is waiting to be removed from a collection.

    Reads liveness from the dispatcher rather than relying on the job to sign
    off, because a job cancelled while queued never runs at all.
    """
    with _lock:
        for job_id in [j for j in _pending if not upload_service.is_live(j)]:
            del _pending[job_id]
        return [ids for cid, ids in _pending.values() if cid == collection_id]


def is_removing(collection_id: str) -> bool:
    """True while a delete job is queued or running for this collection."""
    return bool(_live_removals(collection_id))


def without_pending_removals(collection_id: str, existing: Dict[str, str]) -> Dict[str, str]:
    """Drop documents about to be deleted from a ``{content_hash: document_id}`` map."""
    removals = _live_removals(collection_id)
    if not removals:
        return existing
    if any(ids is None for ids in removals):
        return {}
    going = set().union(*removals)
    return {h: d for h, d in existing.items() if d not in going}


def _batches(documents: List[Dict[str, Any]]) -> Iterator[List[Dict[str, Any]]]:
    batch: List[Dict[str, Any]] = []
    chunks = 0
    for doc in documents:
        size = doc.get("num_chunks") or 0
        if batch and (len(batch) >= BATCH_DOCUMENTS or chunks + size > BATCH_CHUNKS):
            yield batch
            batch, chunks = [], 0
        batch.append(doc)
        chunks += size
    if batch:
        yield batch


def _progress_detail(done: int, total: int) -> str:
    return f"Deleted {done:,} of {total:,} source{'' if total == 1 else 's'}"


def _summary(documents: List[Dict[str, Any]], done: int, chunks_deleted: int,
             failed_files: List[Dict[str, str]], not_found: int) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "documents_processed": done,
        "total_chunks": chunks_deleted,
        "failed_files": failed_files,
        "not_found": not_found,
    }
    # A job for one source can name it when it reports back.
    if len(documents) == 1:
        summary["filename"] = documents[0].get("filename")
    return summary


def _run(job_id: int, collection_id: str, document_ids: Optional[List[str]],
         actor: Optional[str], requested_at: float) -> None:
    """Job body. Raising lets the dispatcher mark the job failed."""
    try:
        _run_removal(job_id, collection_id, document_ids, actor, requested_at)
    finally:
        with _lock:
            _pending.pop(job_id, None)  # a list of 75,000 ids is not worth keeping


def _run_removal(job_id: int, collection_id: str, document_ids: Optional[List[str]],
                 actor: Optional[str], requested_at: float) -> None:
    store = indexer_manager.get_indexer(collection_id).vector_store.metadata_store

    if upload_service.is_cancelled(job_id):
        upload_service.finish_job(job_id, _summary([], 0, 0, [], 0), cancelled=True, processed=0)
        return

    # Read here, not when it was asked for: jobs queued ahead of this one have
    # added to the collection since, and "everything" has to include them.
    with collection_write_lock(collection_id):
        existing = store.list_document_ids()
        if document_ids is None or set(document_ids) >= set(existing):
            documents = list(store.get_documents_info(existing).values())
            _clear_collection(job_id, collection_id, documents, actor, requested_at,
                              not_found=len(set(document_ids or []) - set(existing)))
            return

    found = store.get_documents_info(document_ids)
    documents = [found[d] for d in document_ids if d in found]
    _remove_in_batches(job_id, collection_id, documents, actor, requested_at,
                       not_found=len(document_ids) - len(documents))


def _remove_in_batches(job_id: int, collection_id: str, documents: List[Dict[str, Any]],
                       actor: Optional[str], requested_at: float, not_found: int) -> None:
    indexer = indexer_manager.get_indexer(collection_id)
    total = len(documents)
    chunks_total = sum(d.get("num_chunks") or 0 for d in documents)
    done = chunks_deleted = 0
    cancelled = False
    failed_files: List[Dict[str, str]] = []
    upload_service.report_files(job_id, PHASE, 0, total, _progress_detail(0, total), 0, chunks_total)

    try:
        for batch in _batches(documents):
            # Between batches, so the sources still listed are whole.
            if upload_service.is_cancelled(job_id):
                cancelled = True
                break
            outcome = governance.remove_documents(collection_id, batch, actor=actor, written_before=requested_at)
            done += len(batch)
            chunks_deleted += outcome["chunks_deleted"]
            failed_files.extend(outcome["failed_files"])
            upload_service.report_files(
                job_id, PHASE, done, total, _progress_detail(done, total), chunks_deleted, chunks_total,
            )
    except Exception:
        # The dispatcher reports the failure; keeping what did go is ours. If
        # this save fails too, the index is repaired when it is next opened.
        app_db.update_upload_job(job_id, processed_files=done)
        try:
            indexer.save_index()
        except Exception as e:
            logger.error(f"Could not save the index after delete job {job_id} failed: {e}")
        raise

    if done:
        upload_service.report_files(
            job_id, "saving", done, total, "Saving the index", chunks_deleted, chunks_total,
        )
        indexer.save_index()

    upload_service.finish_job(
        job_id, _summary(documents, done, chunks_deleted, failed_files, not_found),
        cancelled=cancelled, processed=done,
        message=f"Stopped after deleting {done:,} of {total:,} sources. The rest were kept.",
    )


def _clear_collection(job_id: int, collection_id: str, documents: List[Dict[str, Any]],
                      actor: Optional[str], requested_at: float, not_found: int) -> None:
    """Empty a collection, which needs none of the per-document index work.

    Starting a new index and emptying the tables takes moments however large
    the collection is. It also cannot stop half way, so a cancellation that
    arrives from here on has no effect: with the index gone, the files are all
    that is left of these sources, and leaving them would only strand them.
    """
    indexer = indexer_manager.get_indexer(collection_id)
    total = len(documents)
    chunks_total = sum(d.get("num_chunks") or 0 for d in documents)

    upload_service.report_files(job_id, PHASE, 0, total, "Clearing the search index", 0, chunks_total)
    indexer.vector_store.clear_index()
    indexer.vector_store.metadata_store.clear_chunk_boosts()
    collection_service.remove_documents(collection_id)
    governance.invalidate_review_summary()
    governance._invalidate_answers(collection_id)
    governance.record_removals(collection_id, documents, actor=actor)

    failed_files: List[Dict[str, str]] = []
    for start in range(0, total, BATCH_FILES):
        batch = documents[start:start + BATCH_FILES]
        failed_files.extend(governance.remove_source_files(collection_id, batch, written_before=requested_at))
        done = min(start + BATCH_FILES, total)
        upload_service.report_files(
            job_id, PHASE, done, total, f"Removed {done:,} of {total:,} stored files", chunks_total, chunks_total,
        )

    upload_service.finish_job(
        job_id, _summary(documents, total, chunks_total, failed_files, not_found), processed=total,
    )
    logger.info(f"Cleared collection '{collection_id}': {total} documents (job {job_id})")
