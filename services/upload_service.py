"""Background upload service for processing large files in a separate thread.

Handles:
- File staging
- Background document indexing (in separate thread that survives page refreshes)
- Progress tracking via upload_jobs table
- Real-time progress events via SSE (v4.0)
"""

import logging
import threading
import shutil
import json
import asyncio
import queue
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional, Dict, Callable, Any, Tuple
from datetime import datetime
from dataclasses import dataclass, field, asdict
from enum import Enum

from config import settings
from services import audit, storage_quota
from services.app_database import app_db
from services.indexer_manager import indexer_manager
from services.collection_service import collection_service
from services.indexing import ChunkBatcher
from services.link_fetcher import fetch_link

logger = logging.getLogger(__name__)


class UploadPhase(str, Enum):
    """Upload processing phases for granular progress tracking."""

    PENDING = "pending"
    EXTRACTING = "extracting"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    SAVING = "saving"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ProgressEvent:
    """Real-time progress event for SSE streaming."""

    job_id: int
    event_type: str  # phase_start, phase_progress, file_complete, job_complete, error
    phase: str
    current_file: Optional[str] = None
    file_index: int = 0
    total_files: int = 0
    phase_progress: int = 0  # 0-100 within phase
    phase_detail: Optional[str] = None
    chunks_processed: int = 0
    chunks_total: int = 0
    overall_percent: float = 0.0
    error: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_sse(self) -> str:
        """Format as SSE event string."""
        data = json.dumps(asdict(self))
        return f"event: {self.event_type}\ndata: {data}\n\n"


@dataclass
class _BulkFileWork:
    """One file in a bulk index job, with job-type-specific hooks.

    stage() runs in an extraction worker thread and must return the path to
    index (copying into the library first when the job requires it).
    finalize(indexer, doc_metadata) runs after the document is persisted;
    cleanup() runs when the file fails (e.g. to remove a staged copy).
    """

    display_name: str
    stage: Callable[[], Path]
    finalize: Optional[Callable[[Any, Any], None]] = None
    cleanup: Optional[Callable[[], None]] = None
    # Bytes of the source, known before staging so the storage cap can refuse
    # the file without copying it; held_bytes is the reservation taken
    # against the cap until the row commits or the file fails.
    size_bytes: int = 0
    held_bytes: int = 0


@dataclass
class _QueuedJob:
    """A job that has a row and a plan but has not been given a thread yet.

    Indexing used to refuse a second job for a collection with a 409 and
    leave the caller to retry by hand, which is why adding a folder right
    after adding files so often looked broken. Jobs now wait their turn
    here instead: FIFO, at most one running per collection, and never more
    than ``MAX_CONCURRENT_INDEX_JOBS`` running at once.
    """

    job_id: int
    collection_id: str
    target: Callable[..., None]
    args: tuple


def _size_of(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


@dataclass
class FolderScan:
    """What a folder scan picked up: the files to index, and how many were
    left out because nothing can index them (unsupported name, over the
    size limit)."""
    files: List[str]
    skipped_unsupported: int = 0

    def __iter__(self):
        return iter(self.files)

    def __len__(self) -> int:
        return len(self.files)


class RepoIndexResult(tuple):
    """``(job_id, files_found, skipped_unchanged)`` as before, so callers
    that unpack three values keep working; ``skipped_unsupported`` rides
    along as an attribute."""

    skipped_unsupported: int

    def __new__(cls, job_id: Optional[int], files_found: int, skipped_unchanged: int,
                skipped_unsupported: int = 0):
        self = super().__new__(cls, (job_id, files_found, skipped_unchanged))
        self.skipped_unsupported = skipped_unsupported
        return self

    @property
    def job_id(self) -> Optional[int]:
        return self[0]

    @property
    def files_found(self) -> int:
        return self[1]

    @property
    def skipped_unchanged(self) -> int:
        return self[2]


class UploadService:
    """Manages background document upload and indexing.

    Uses threading to ensure uploads continue even when the client
    disconnects or refreshes the page.

    v4.0: Adds real-time progress events via SSE.
    """

    # A waiting list this long means something is submitting in a loop; past
    # here we refuse rather than accept work we will not get to.
    MAX_QUEUED_JOBS = 50

    def __init__(self):
        self._active_threads: Dict[int, threading.Thread] = {}
        # job_id -> collection_id for every job currently holding a thread,
        # so the dispatcher can keep one running job per collection without
        # a database round trip.
        self._active_collections: Dict[int, str] = {}
        self._queue: deque = deque()  # _QueuedJob, FIFO
        self._lock = threading.Lock()
        # SSE event queues per job (for real-time streaming)
        self._event_queues: Dict[int, List[queue.Queue]] = {}
        self._queues_lock = threading.Lock()
        # Cancellation flags
        self._cancel_flags: Dict[int, bool] = {}

    def active_job_count(self) -> int:
        with self._lock:
            return len(self._active_threads)

    def queued_job_count(self) -> int:
        with self._lock:
            return len(self._queue)

    def queue_position(self, job_id: int) -> Optional[int]:
        """1-based place in line, or None if the job is not waiting."""
        with self._lock:
            for position, queued in enumerate(self._queue, 1):
                if queued.job_id == job_id:
                    return position
        return None

    def _check_queue_capacity(self) -> None:
        """Refuse submission only when the waiting list itself is full.

        Every job funnels through one embedding lock, so running many at
        once just shuffles the queue while starving live search — the
        concurrency cap lives in the dispatcher instead, which holds extra
        jobs back rather than rejecting them. Raises RuntimeError, which
        the routers already translate to an HTTP conflict.
        """
        with self._lock:
            waiting = len(self._queue)
        if waiting >= self.MAX_QUEUED_JOBS:
            raise RuntimeError(
                f"{waiting} indexing jobs are already waiting to run. "
                "Wait for the queue to drain before adding more."
            )

    def _submit(self, job: _QueuedJob) -> None:
        """Put a job on the queue and start it if a slot is free."""
        with self._lock:
            self._cancel_flags.setdefault(job.job_id, False)
            self._queue.append(job)
        self._dispatch()

    def _dispatch(self) -> None:
        """Start as many waiting jobs as the caps allow.

        Picks the first waiting job whose collection has nothing running,
        so a queue headed by a busy collection does not block jobs for
        other collections behind it.
        """
        while True:
            with self._lock:
                cap = max(0, int(settings.max_concurrent_index_jobs or 0))
                if cap and len(self._active_threads) >= cap:
                    return
                busy = set(self._active_collections.values())
                picked = next(
                    (q for q in self._queue if q.collection_id not in busy), None
                )
                if picked is None:
                    return
                self._queue.remove(picked)
                waiting = len(self._queue)
                thread = threading.Thread(
                    target=self._run_job,
                    args=(picked,),
                    name=f"index-job-{picked.job_id}",
                    daemon=False,
                )
                self._active_threads[picked.job_id] = thread
                self._active_collections[picked.job_id] = picked.collection_id
            thread.start()
            logger.info(
                f"Started index job {picked.job_id} for collection "
                f"'{picked.collection_id}' ({waiting} still queued)"
            )

    def _run_job(self, job: _QueuedJob) -> None:
        """Thread body: run the job, then always free its slot and dispatch.

        The slot is released here rather than inside the pipeline so that a
        job which dies before the pipeline starts (a missing documents
        directory, say) cannot strand its collection permanently.
        """
        try:
            job.target(*job.args)
        except Exception as e:
            logger.error(f"Index job {job.job_id} crashed: {e}", exc_info=True)
            try:
                app_db.update_upload_job(
                    job.job_id, status="failed", error=str(e), current_file=None
                )
            except Exception:
                logger.exception(f"Could not mark job {job.job_id} failed")
            self._broadcast_event(ProgressEvent(
                job_id=job.job_id,
                event_type="job_error",
                phase=UploadPhase.FAILED.value,
                error=str(e),
            ))
        finally:
            with self._lock:
                self._active_threads.pop(job.job_id, None)
                self._active_collections.pop(job.job_id, None)
                self._cancel_flags.pop(job.job_id, None)
            self._dispatch()

    def recover_orphaned_jobs(self) -> Dict[str, int]:
        """Close out jobs the last process left mid-flight. Call once at startup.

        Upload jobs live in threads and reindex jobs in asyncio tasks;
        neither survives a restart, but their rows do. Until this ran, a
        restart mid-index left a job that showed as running forever in the
        drawer, and the guard that keeps one job per collection refused
        every future job for that collection.
        """
        try:
            counts = app_db.fail_interrupted_jobs(
                "Interrupted - the server restarted while this job was running. "
                "Add the sources again to finish indexing them."
            )
        except Exception as e:
            logger.error(f"Could not reconcile interrupted jobs: {e}")
            return {"upload_jobs": 0, "reindex_jobs": 0}
        if counts.get("upload_jobs") or counts.get("reindex_jobs"):
            logger.warning(
                f"Marked {counts['upload_jobs']} interrupted index job(s) and "
                f"{counts['reindex_jobs']} interrupted re-index job(s) as failed"
            )
        return counts

    def subscribe_to_events(self, job_id: int) -> queue.Queue:
        """Subscribe to real-time progress events for a job.

        Returns a queue that will receive ProgressEvent objects.
        """
        event_queue = queue.Queue()
        with self._queues_lock:
            if job_id not in self._event_queues:
                self._event_queues[job_id] = []
            self._event_queues[job_id].append(event_queue)
        return event_queue

    def unsubscribe_from_events(self, job_id: int, event_queue: queue.Queue):
        """Unsubscribe from progress events."""
        with self._queues_lock:
            if job_id in self._event_queues:
                try:
                    self._event_queues[job_id].remove(event_queue)
                    if not self._event_queues[job_id]:
                        del self._event_queues[job_id]
                except ValueError:
                    pass

    def _broadcast_event(self, event: ProgressEvent):
        """Broadcast progress event to all subscribers."""
        with self._queues_lock:
            queues = self._event_queues.get(event.job_id, [])
            for q in queues:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass  # Skip if queue is full

    def cancel_job(self, job_id: int, force: bool = False) -> str:
        """Cancel a job, whether it is waiting in the queue or already running.

        Returns what happened, so the caller can tell the person something
        true: "cancelled" (the job is finished and will not run), "cancelling"
        (a running job was asked to stop and will at the next file boundary),
        or "" (no such job here).
        """
        # A job still in the queue has touched nothing: drop it outright
        # rather than leaving it to start and immediately stop.
        with self._lock:
            waiting = next((q for q in self._queue if q.job_id == job_id), None)
            if waiting is not None:
                self._queue.remove(waiting)
                self._cancel_flags.pop(job_id, None)
        if waiting is not None:
            logger.info(f"Cancelled queued job {job_id} before it started")
            app_db.update_upload_job(
                job_id, status="cancelled", error="Cancelled before it started",
                current_file=None,
            )
            self._broadcast_event(ProgressEvent(
                job_id=job_id,
                event_type="job_cancelled",
                phase="cancelled",
                error="Cancelled before it started",
            ))
            return "cancelled"

        with self._lock:
            if job_id in self._active_threads:
                self._cancel_flags[job_id] = True
                running = True
            else:
                running = False
        if running:
            logger.info(f"Cancellation requested for job {job_id}")
            # Tell every listener immediately. A running job stops at the
            # next file boundary, which can be a while on a large file, and
            # a Cancel button that visibly does nothing gets clicked again.
            self._broadcast_event(ProgressEvent(
                job_id=job_id,
                event_type="job_cancelling",
                phase="cancelling",
            ))
            return "cancelling"

        # Thread not found - if force=True, mark as cancelled directly in DB
        if force:
            logger.info(f"Force-cancelling orphaned job {job_id} (thread not active)")
            app_db.update_upload_job(
                job_id,
                status="cancelled",
                error="Force cancelled (job thread was not active)",
                current_file=None,
            )
            # Broadcast cancellation event for any listeners
            self._broadcast_event(ProgressEvent(
                job_id=job_id,
                event_type="job_cancelled",
                phase="cancelled",
                error="Force cancelled (job thread was not active)",
            ))
            return "cancelled"

        return ""

    def _is_cancelled(self, job_id: int) -> bool:
        """Check if job has been cancelled."""
        return self._cancel_flags.get(job_id, False)

    def cancel_requested(self, job_id: int) -> bool:
        """True while a running job is stopping but has not stopped yet."""
        with self._lock:
            return bool(self._cancel_flags.get(job_id))

    def get_job_status(self, job_id: int) -> Optional[dict]:
        """Get upload job status with progress percentage.

        Args:
            job_id: Job ID

        Returns:
            Job details with progress_percent, or None if not found
        """
        job = app_db.get_upload_job(job_id)
        if not job:
            return None

        # Calculate progress percentage
        if job["total_files"] > 0:
            progress = (job["processed_files"] / job["total_files"]) * 100
        else:
            progress = 0
        if job["status"] in ("completed", "failed", "cancelled"):
            # A finished job's bar should not sit at 97% forever because the
            # last throttled progress write lost a race with the final one.
            progress = 100 if job["status"] == "completed" else progress

        # Parse result_summary if present
        result_summary = None
        if job.get("result_summary"):
            try:
                result_summary = json.loads(job["result_summary"])
            except json.JSONDecodeError:
                pass

        return {
            "job_id": job["id"],
            "collection_id": job["collection_id"],
            "status": job["status"],
            "total_files": job["total_files"],
            "processed_files": job["processed_files"],
            "current_file": job["current_file"],
            "progress_percent": round(progress, 1),
            "error": job["error"],
            "result_summary": result_summary,
            "started_at": job["started_at"],
            "completed_at": job["completed_at"],
            # v4.0: Granular progress
            "phase": job.get("phase"),
            "phase_progress": job.get("phase_progress"),
            "phase_detail": job.get("phase_detail"),
            "chunks_processed": job.get("chunks_processed"),
            "chunks_total": job.get("chunks_total"),
            # Job type: 'upload' for browser uploads, 'index' for local file indexing
            "job_type": job.get("job_type", "upload"),
            # Place in line while waiting for a slot (None once running)
            "queue_position": self.queue_position(job["id"]),
            # A running job that has been asked to stop
            "cancel_requested": self.cancel_requested(job["id"]),
        }


    def submit_job(
        self,
        collection_id: str,
        job_type: str,
        total_files: int,
        target: Callable[..., None],
        args: Tuple = (),
    ) -> int:
        """Queue a job of another kind (e.g. a collection import) on the same
        dispatcher, so it shows in the jobs drawer, respects the one-job-per-
        collection rule and can be cancelled like any index job.

        `target(job_id, *args)` runs on the job thread; it reports through
        report_progress()/finish_job() and may check is_cancelled(job_id).
        Raises RuntimeError when the waiting list is full.
        """
        self._check_queue_capacity()
        job_id = app_db.create_upload_job(collection_id, total_files, job_type=job_type)
        self._submit(_QueuedJob(
            job_id=job_id,
            collection_id=collection_id,
            target=target,
            args=(job_id, *args),
        ))
        return job_id

    def is_cancelled(self, job_id: int) -> bool:
        return self._is_cancelled(job_id)

    def report_progress(self, job_id: int, phase: str, percent: float, detail: str,
                        done: int = 0, total: int = 0) -> None:
        """Record and broadcast progress for a job started with submit_job()."""
        app_db.update_upload_job(
            job_id, status="running", phase=phase, phase_progress=int(percent),
            phase_detail=detail, chunks_processed=done, chunks_total=total,
        )
        self._broadcast_event(ProgressEvent(
            job_id=job_id, event_type="phase_progress", phase=phase,
            phase_progress=percent, phase_detail=detail,
            chunks_processed=done, chunks_total=total, overall_percent=percent,
        ))

    def finish_job(self, job_id: int, summary: Dict[str, Any], cancelled: bool = False) -> None:
        """Mark a submit_job() job completed (or cancelled) and tell listeners."""
        status = "cancelled" if cancelled else "completed"
        app_db.update_upload_job(
            job_id, status=status, processed_files=0 if cancelled else 1,
            current_file=None, phase=status, result_summary=json.dumps(summary),
            error="Cancelled" if cancelled else None,
        )
        self._broadcast_event(ProgressEvent(
            job_id=job_id, event_type="job_cancelled" if cancelled else "job_complete",
            phase=status, overall_percent=100.0,
        ))

    def start_local_index(
        self,
        file_paths: List[str],
        collection_id: str = "default",
        copy_to_library: bool = False,
        uploaded_by: Optional[str] = None,
        incremental: bool = False,
    ) -> Optional[int]:
        """Start a background job to index local files.

        Args:
            file_paths: List of absolute file paths to index
            collection_id: Target collection ID
            copy_to_library: If True, copy files to library; otherwise index in-place
            uploaded_by: Identity that started the job. Captured here, on the
                request thread, because the worker thread has no request
                context to read it from; recorded on every document row.
            incremental: Hash the files first and leave out those whose
                bytes are already in the collection. Off by default: the
                existing callers expect a job for every call.

        Returns:
            Job ID for tracking progress, or None when ``incremental`` found
            nothing new to index (no job is created then).
        """
        if incremental:
            file_paths, unchanged, _ = self._split_unchanged(list(file_paths), collection_id)
            if not file_paths:
                logger.info(
                    f"Local index into '{collection_id}' is up to date: "
                    f"{len(unchanged)} unchanged files, nothing queued"
                )
                return None

        self._check_queue_capacity()

        # Create job record with job_type='index' for local file indexing
        job_id = app_db.create_upload_job(collection_id, len(file_paths), job_type="index")
        audit.record("document.index_job", actor=uploaded_by, collection_id=collection_id,
                     target=str(job_id),
                     detail={"kind": "local", "files": len(file_paths),
                             "copy_to_library": copy_to_library,
                             "sample": [Path(p).name for p in file_paths[:10]]})

        # Queued, not started: the dispatcher runs it as soon as this
        # collection is free and a slot is open.
        self._submit(_QueuedJob(
            job_id=job_id,
            collection_id=collection_id,
            target=self._run_local_index,
            args=(job_id, file_paths, collection_id, copy_to_library, uploaded_by),
        ))
        logger.info(f"Submitted local index job {job_id}: {len(file_paths)} files")

        return job_id

    def _run_local_index(
        self,
        job_id: int,
        file_paths: List[str],
        collection_id: str,
        copy_to_library: bool,
        uploaded_by: Optional[str] = None,
    ):
        """Run local file indexing in background thread."""
        documents_dir = indexer_manager.get_documents_path(collection_id) if copy_to_library else None
        # Staging resolves filename collisions with an exists() loop; serialize
        # it so two same-named files staged by different extraction workers
        # can't race into the same destination path.
        copy_lock = threading.Lock()

        def make_work(source_path: Path) -> _BulkFileWork:
            if copy_to_library:
                def stage() -> Path:
                    with copy_lock:
                        final_path = documents_dir / source_path.name
                        counter = 1
                        while final_path.exists():
                            final_path = documents_dir / f"{source_path.stem}_{counter}{source_path.suffix}"
                            counter += 1
                        shutil.copy2(str(source_path), str(final_path))
                    return final_path

                return _BulkFileWork(display_name=source_path.name, stage=stage,
                                     size_bytes=_size_of(source_path))

            def stage() -> Path:
                return source_path

            def finalize(indexer, doc_metadata):
                indexer.vector_store.metadata_store.update_document_source(
                    doc_metadata.document_id,
                    source_path=str(source_path.absolute()),
                    source_type="local_reference",
                )

            return _BulkFileWork(display_name=source_path.name, stage=stage, finalize=finalize,
                                 size_bytes=_size_of(source_path))

        logger.info(
            f"Starting local index job {job_id}: {len(file_paths)} files, copy={copy_to_library}"
        )
        works = [make_work(Path(fp)) for fp in file_paths]
        self._process_bulk_job(job_id, works, collection_id, uploaded_by=uploaded_by)


    def start_link_index(
        self,
        urls: List[str],
        collection_id: str = "default",
        uploaded_by: Optional[str] = None,
    ) -> int:
        """Start a background job that fetches and indexes the content behind links.

        Each URL is fetched from an extraction worker (so several download
        at once), saved into the collection's documents directory under a
        name that says where it came from, and indexed exactly like an
        uploaded file of that type. The document keeps the URL as its
        source_path with source_type='url'.

        Args:
            urls: Links already passed through link_fetcher.validate_link
            collection_id: Target collection ID
            uploaded_by: Identity that started the job (see start_local_index)
        """
        self._check_queue_capacity()
        job_id = app_db.create_upload_job(collection_id, len(urls), job_type="index")
        audit.record("document.index_job", actor=uploaded_by, collection_id=collection_id,
                     target=str(job_id),
                     detail={"kind": "link", "files": len(urls), "sample": urls[:10]})
        self._submit(_QueuedJob(
            job_id=job_id,
            collection_id=collection_id,
            target=self._run_link_index,
            args=(job_id, urls, collection_id, uploaded_by),
        ))
        logger.info(f"Submitted link index job {job_id}: {len(urls)} links")
        return job_id

    def _run_link_index(
        self,
        job_id: int,
        urls: List[str],
        collection_id: str,
        uploaded_by: Optional[str] = None,
    ):
        """Run link fetching + indexing in the background thread."""
        documents_dir = indexer_manager.get_documents_path(collection_id)
        documents_dir.mkdir(parents=True, exist_ok=True)
        save_lock = threading.Lock()

        def make_work(url: str) -> _BulkFileWork:
            saved: Dict[str, Path] = {}
            work = _BulkFileWork(display_name=url, stage=lambda: None)

            def stage() -> Path:
                fetched = fetch_link(url)
                # The size is only known once the body is in hand, so the
                # storage cap is taken here rather than before staging; a
                # refusal releases nothing because nothing was written yet.
                work.held_bytes += storage_quota.take(
                    collection_id, len(fetched.body), fetched.filename
                )
                with save_lock:
                    final_path = documents_dir / fetched.filename
                    stem, suffix = final_path.stem, final_path.suffix
                    counter = 1
                    while final_path.exists():
                        final_path = documents_dir / f"{stem}_{counter}{suffix}"
                        counter += 1
                    final_path.write_bytes(fetched.body)
                saved["path"] = final_path
                return final_path

            def finalize(indexer, doc_metadata):
                indexer.vector_store.metadata_store.update_document_source(
                    doc_metadata.document_id, source_path=url, source_type="url",
                )
                doc_metadata.source_path = url
                doc_metadata.source_type = "url"

            def cleanup():
                path = saved.pop("path", None)
                if path is not None:
                    path.unlink(missing_ok=True)

            work.stage = stage
            work.finalize = finalize
            work.cleanup = cleanup
            return work

        logger.info(f"Starting link index job {job_id}: {len(urls)} links")
        works = [make_work(url) for url in urls]
        self._process_bulk_job(job_id, works, collection_id, uploaded_by=uploaded_by)

    # Folder-synced documents carry this source_type so a later sync of the
    # same folder can tell its own documents apart from uploads and from
    # in-place local references that happen to live under the same path.
    FOLDER_SYNC_SOURCE_TYPE = "folder_sync"

    # Default exclude patterns for common non-code directories, build
    # output, caches, lockfiles, binaries, archives and fonts. SVG is out
    # too: generated SVGs run to megabytes; a picture uploaded on its own
    # still goes through the image path.
    DEFAULT_REPO_EXCLUDES = [
        '**/node_modules/**', '**/.git/**', '**/__pycache__/**',
        '**/venv/**', '**/.venv/**', '**/dist/**', '**/build/**',
        '**/*.pyc', '**/.DS_Store', '**/Thumbs.db',
        # IDE and framework output
        '**/.idea/**', '**/.vscode/**', '**/.next/**', '**/.nuxt/**',
        '**/.svelte-kit/**', '**/coverage/**', '**/target/**', '**/bin/**',
        '**/obj/**', '**/.terraform/**', '**/.tox/**', '**/.mypy_cache/**',
        '**/.pytest_cache/**', '**/.ruff_cache/**',
        # Generated / lock files
        '**/*.min.js', '**/*.min.css', '**/*.map', '**/*.lock',
        '**/package-lock.json', '**/yarn.lock', '**/pnpm-lock.yaml',
        '**/Cargo.lock', '**/poetry.lock',
        # Binaries and archives
        '**/*.pyo', '**/*.so', '**/*.dll', '**/*.dylib', '**/*.exe', '**/*.o',
        '**/*.a', '**/*.class', '**/*.jar', '**/*.war', '**/*.zip', '**/*.tar',
        '**/*.gz', '**/*.7z', '**/*.rar',
        # Fonts and icons
        '**/*.woff', '**/*.woff2', '**/*.ttf', '**/*.eot', '**/*.ico', '**/*.svg',
    ]

    # Files larger than this are left out of a folder scan: they are not
    # source code, and extracting them would stall the job.
    SCAN_MAX_FILE_BYTES = 25 * 1024 * 1024

    @staticmethod
    def _scan_folder(
        repo: Path,
        recursive: bool = True,
        file_extensions: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
    ) -> "FolderScan":
        """Walk a folder and return the files a repo index would pick up.

        Without an explicit ``file_extensions`` filter, files the extractor
        cannot index (judged by name: suffix or well-known basename) and
        files over 25 MiB are skipped and counted in
        ``FolderScan.skipped_unsupported`` rather than queued to fail.
        Symlinks pointing outside the folder are never followed.
        """
        import os
        import fnmatch
        from services.document_extractor import is_supported_filename

        excludes = (exclude_patterns or []) + UploadService.DEFAULT_REPO_EXCLUDES
        extensions = {e.lower() for e in (file_extensions or [])}
        root_resolved = repo.resolve()
        max_bytes = UploadService.SCAN_MAX_FILE_BYTES

        def should_exclude(path: Path, is_dir: bool = False) -> bool:
            # `**/x/**` patterns need a slash before and after `x`, which a
            # top-level entry's relative path lacks: test it with those too.
            rel = path.relative_to(repo).as_posix()
            candidates = [rel, '/' + rel]
            if is_dir:
                candidates += [rel + '/', '/' + rel + '/']
            return any(
                fnmatch.fnmatch(candidate, pattern)
                for pattern in excludes for candidate in candidates
            )

        def should_include(path: Path) -> bool:
            if extensions:
                return path.suffix.lower() in extensions
            return True  # Include all if no filter

        def inside_root(path: Path) -> bool:
            """A symlink that resolves outside the folder is not part of it."""
            if not path.is_symlink():
                return True
            try:
                return path.resolve().is_relative_to(root_resolved)
            except OSError:
                return False

        skipped_unsupported = 0

        def admit(fp: Path) -> Optional[bool]:
            """True to index, False to count as skipped, None to ignore."""
            nonlocal skipped_unsupported
            if should_exclude(fp) or not should_include(fp):
                return None
            if not inside_root(fp):
                return None
            if extensions:
                return True  # the caller asked for these by name
            if not is_supported_filename(fp.name):
                skipped_unsupported += 1
                return False
            try:
                if fp.stat().st_size > max_bytes:
                    skipped_unsupported += 1
                    return False
            except OSError:
                return None
            return True

        files: List[str] = []
        if recursive:
            for root, dirs, names in os.walk(repo):
                dirs[:] = [
                    d for d in dirs
                    if not should_exclude(Path(root) / d, is_dir=True) and inside_root(Path(root) / d)
                ]
                for f in names:
                    fp = Path(root) / f
                    if admit(fp):
                        files.append(str(fp))
        else:
            for fp in repo.iterdir():
                if fp.is_file() and admit(fp):
                    files.append(str(fp))
        return FolderScan(files=files, skipped_unsupported=skipped_unsupported)

    @staticmethod
    def _hash_files(file_paths: List[str]) -> Dict[str, str]:
        """``{path: sha256}`` for every readable file, streamed in blocks.

        The same hash the indexer admits documents by, so a match here means
        the exact bytes are already in the index. Unreadable files are left
        out and go through the job, which reports the failure properly.
        """
        from services.governance import content_hash_of

        hashes: Dict[str, str] = {}
        for fp in file_paths:
            try:
                hashes[fp] = content_hash_of(Path(fp))
            except OSError as e:
                logger.warning(f"Could not hash {fp}: {e}")
        return hashes

    @staticmethod
    def _split_unchanged(
        file_paths: List[str], collection_id: str
    ) -> Tuple[List[str], List[str], Dict[str, str]]:
        """Partition files into (to_index, unchanged, hashes) against a collection.

        A file is unchanged when a document with its content hash is already
        in the collection, whatever name or path it was added under.
        """
        hashes = UploadService._hash_files(file_paths)
        store = indexer_manager.get_indexer(collection_id).vector_store.metadata_store
        existing = store.get_document_ids_by_content_hashes(hashes.values())
        to_index: List[str] = []
        unchanged: List[str] = []
        for fp in file_paths:
            if hashes.get(fp) in existing:
                unchanged.append(fp)
            else:
                to_index.append(fp)
        return to_index, unchanged, hashes

    def _queue_repo_job(
        self,
        files_to_index: List[str],
        repo_path: str,
        collection_id: str,
        uploaded_by: Optional[str],
        audit_detail: Dict[str, Any],
    ) -> int:
        """Create the job row for a folder index and put it on the queue."""
        self._check_queue_capacity()
        job_id = app_db.create_upload_job(collection_id, len(files_to_index), job_type="index")
        audit.record("document.index_job", actor=uploaded_by, collection_id=collection_id,
                     target=str(job_id), detail={"kind": "repo", "path": repo_path, **audit_detail})
        self._submit(_QueuedJob(
            job_id=job_id,
            collection_id=collection_id,
            target=self._run_repo_index,
            args=(job_id, files_to_index, repo_path, collection_id, uploaded_by),
        ))
        logger.info(
            f"Submitted repo index job {job_id}: {len(files_to_index)} files from {repo_path}"
        )
        return job_id

    # ── Sync memory ──────────────────────────────────────────────────────

    @staticmethod
    def _sync_memory_key(collection_id: str) -> str:
        return f"sync_folders:{collection_id}"

    def list_sync_folders(self, collection_id: str) -> List[Dict[str, Any]]:
        """Folders that were indexed or synced into a collection, newest first.

        Each entry carries the path, the options it was synced with and the
        counts from the last run, so the UI can offer "Sync again" without
        the person finding the folder a second time.
        """
        remembered = app_db.get_config(self._sync_memory_key(collection_id), {}) or {}
        if not isinstance(remembered, dict):
            return []
        entries = [dict(v, path=k) for k, v in remembered.items() if isinstance(v, dict)]
        entries.sort(key=lambda e: e.get("last_synced_at") or "", reverse=True)
        return entries

    def _remember_sync(self, collection_id: str, root: Path, **fields: Any) -> Dict[str, Any]:
        remembered = app_db.get_config(self._sync_memory_key(collection_id), {}) or {}
        if not isinstance(remembered, dict):
            remembered = {}
        entry = {"last_synced_at": datetime.utcnow().isoformat(), **fields}
        remembered[str(root)] = entry
        app_db.set_config(self._sync_memory_key(collection_id), remembered)
        return dict(entry, path=str(root))

    def forget_sync_folder(self, collection_id: str, path: str) -> bool:
        """Drop a remembered folder. Documents are untouched."""
        key = self._sync_memory_key(collection_id)
        remembered = app_db.get_config(key, {}) or {}
        root = str(Path(path).resolve())
        if not isinstance(remembered, dict) or root not in remembered:
            return False
        remembered.pop(root)
        app_db.set_config(key, remembered)
        return True

    # ── Repo / folder indexing ───────────────────────────────────────────

    def start_repo_index(
        self,
        repo_path: str,
        collection_id: str = "default",
        recursive: bool = True,
        file_extensions: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        uploaded_by: Optional[str] = None,
        incremental: bool = True,
    ) -> "RepoIndexResult":
        """Start a background job to index a repository/folder.

        Args:
            repo_path: Path to the repository/folder to index
            collection_id: Target collection ID
            recursive: Whether to scan subdirectories
            file_extensions: List of extensions to include (e.g., ['.py', '.js'])
            exclude_patterns: Glob patterns to exclude
            incremental: Skip files whose exact bytes are already in the
                collection (looked up by content hash), so re-adding the
                same folder does not re-extract and re-embed everything.

        Returns:
            (job_id, files_found, skipped_unchanged) - the caller needs the
            counts to tell the person what was picked up; the job row carries
            the indexed count too, but not before the response goes out.
            ``job_id`` is None when every file was unchanged: nothing was
            queued and the collection is already up to date. The result
            unpacks as that 3-tuple; its ``skipped_unsupported`` attribute
            is the number of files the scan left out as unindexable.
        """
        repo = Path(repo_path)
        if not repo.exists() or not repo.is_dir():
            raise ValueError(f"Invalid repository path: {repo_path}")

        scan = self._scan_folder(repo, recursive, file_extensions, exclude_patterns)
        files_found = scan.files
        if not files_found:
            if scan.skipped_unsupported:
                raise ValueError(
                    f"No indexable files found in {repo_path} "
                    f"({scan.skipped_unsupported} unsupported or oversized files skipped)"
                )
            raise ValueError(f"No matching files found in {repo_path}")

        if incremental:
            files_to_index, unchanged, _ = self._split_unchanged(files_found, collection_id)
        else:
            files_to_index, unchanged = list(files_found), []

        job_id: Optional[int] = None
        if files_to_index:
            job_id = self._queue_repo_job(
                files_to_index, repo_path, collection_id, uploaded_by,
                {"files": len(files_to_index), "skipped_unchanged": len(unchanged),
                 "skipped_unsupported": scan.skipped_unsupported},
            )
        else:
            logger.info(
                f"Repo index of {repo_path} into '{collection_id}' is up to date: "
                f"{len(unchanged)} unchanged files, nothing queued"
            )

        try:
            self._remember_sync(
                collection_id, repo.resolve(), job_id=job_id,
                files_found=len(files_found), queued=len(files_to_index),
                skipped_unchanged=len(unchanged), replaced_count=0, pruned_count=0,
                skipped_unsupported=scan.skipped_unsupported,
                recursive=recursive, file_extensions=file_extensions,
                exclude_patterns=exclude_patterns, prune_missing=False,
            )
        except Exception as e:  # memory is a convenience; never fail the index over it
            logger.warning(f"Could not remember sync folder {repo_path}: {e}")

        return RepoIndexResult(job_id, len(files_found), len(unchanged),
                               skipped_unsupported=scan.skipped_unsupported)

    def sync_folder(
        self,
        path: str,
        collection_id: str = "default",
        recursive: bool = True,
        file_extensions: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        prune_missing: bool = False,
        uploaded_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Bring a collection up to date with a folder on disk.

        Three passes, cheapest first, all decided on the request thread:

        * files whose bytes are already indexed are skipped;
        * a file this folder was synced from before, whose bytes changed, has
          its old document removed before the new one is queued (otherwise
          both versions would answer questions);
        * with ``prune_missing``, documents this folder was synced from whose
          file is gone are removed from the index.

        Only new and changed files go through the indexing job. Returns the
        counts the endpoint reports; ``job_id`` is None when nothing needed
        indexing.
        """
        from services import governance

        repo = Path(path)
        if not repo.exists() or not repo.is_dir():
            raise ValueError(f"Invalid folder path: {path}")
        root = repo.resolve()

        scan = self._scan_folder(root, recursive, file_extensions, exclude_patterns)
        files_found = scan.files
        files_to_index, unchanged, hashes = self._split_unchanged(files_found, collection_id)
        # Hashing resolved nothing, so compare on the resolved path too.
        current_hashes = {str(Path(fp).resolve()): h for fp, h in hashes.items()}

        store = indexer_manager.get_indexer(collection_id).vector_store.metadata_store
        tracked = store.list_documents_under_source_root(
            str(root), source_type=self.FOLDER_SYNC_SOURCE_TYPE
        )

        replaced: List[str] = []
        pruned: List[str] = []
        removed_ids: set = set()
        for doc in tracked:
            doc_id = doc["document_id"]
            if doc_id in removed_ids:
                continue
            source_path = doc.get("source_path") or ""
            current = current_hashes.get(source_path)
            if current is not None:
                if current == doc.get("content_hash"):
                    continue  # still the same bytes
                action, bucket = "document.sync_replace", replaced
            elif prune_missing and not Path(source_path).exists():
                action, bucket = "document.sync_prune", pruned
            else:
                continue  # excluded by the filters this time, or prune is off
            try:
                governance.remove_document(
                    collection_id, doc_id, actor=uploaded_by, action=action,
                )
            except KeyError:
                continue  # already gone
            removed_ids.add(doc_id)
            bucket.append(doc.get("filename") or Path(source_path).name)

        job_id: Optional[int] = None
        if files_to_index:
            job_id = self._queue_repo_job(
                files_to_index, str(root), collection_id, uploaded_by,
                {"files": len(files_to_index), "skipped_unchanged": len(unchanged),
                 "skipped_unsupported": scan.skipped_unsupported,
                 "replaced": len(replaced), "pruned": len(pruned), "sync": True},
            )

        audit.record("document.sync_folder", actor=uploaded_by, collection_id=collection_id,
                     target=str(root),
                     detail={"files_found": len(files_found), "queued": len(files_to_index),
                             "skipped_unchanged": len(unchanged),
                             "skipped_unsupported": scan.skipped_unsupported,
                             "replaced": len(replaced),
                             "pruned": len(pruned), "prune_missing": prune_missing,
                             "job_id": job_id})

        remembered: Dict[str, Any] = {}
        try:
            remembered = self._remember_sync(
                collection_id, root, job_id=job_id,
                files_found=len(files_found), queued=len(files_to_index),
                skipped_unchanged=len(unchanged), replaced_count=len(replaced),
                pruned_count=len(pruned), skipped_unsupported=scan.skipped_unsupported,
                recursive=recursive,
                file_extensions=file_extensions, exclude_patterns=exclude_patterns,
                prune_missing=prune_missing,
            )
        except Exception as e:
            logger.warning(f"Could not remember sync folder {root}: {e}")

        logger.info(
            f"Folder sync {root} -> '{collection_id}': {len(files_to_index)} queued, "
            f"{len(unchanged)} unchanged, {len(replaced)} replaced, {len(pruned)} pruned"
        )
        return {
            "status": "queued" if job_id is not None else "up_to_date",
            "job_id": job_id,
            "path": str(root),
            "files_found": len(files_found),
            "queued": len(files_to_index),
            "skipped_unchanged": len(unchanged),
            "skipped_unsupported": scan.skipped_unsupported,
            "replaced": replaced,
            "replaced_count": len(replaced),
            "pruned": pruned,
            "pruned_count": len(pruned),
            "last_synced_at": remembered.get("last_synced_at"),
        }

    def _run_repo_index(
        self,
        job_id: int,
        file_paths: List[str],
        repo_path: str,
        collection_id: str,
        uploaded_by: Optional[str] = None,
    ):
        """Run repository indexing in background thread."""
        documents_dir = indexer_manager.get_documents_path(collection_id)
        repo = Path(repo_path)
        source_type = self.FOLDER_SYNC_SOURCE_TYPE

        def make_work(source_path: Path) -> _BulkFileWork:
            # Create safe filename preserving relative path structure
            try:
                rel_path = source_path.relative_to(repo)
                safe_filename = str(rel_path).replace('/', '_').replace('\\', '_')
            except ValueError:
                safe_filename = source_path.name
            dest_path = documents_dir / safe_filename
            # Where the bytes came from, so a later sync of the same folder
            # can find this document again when the file changes or goes away.
            original = str(source_path.resolve())

            def stage() -> Path:
                shutil.copy2(str(source_path), str(dest_path))
                return dest_path

            def finalize(indexer, doc_metadata):
                indexer.vector_store.metadata_store.update_document_source(
                    doc_metadata.document_id, source_path=original, source_type=source_type,
                )
                doc_metadata.source_path = original
                doc_metadata.source_type = source_type

            def cleanup():
                # Clean up copied file
                try:
                    if dest_path.exists():
                        dest_path.unlink()
                except Exception:
                    pass

            return _BulkFileWork(display_name=safe_filename, stage=stage, finalize=finalize,
                                 cleanup=cleanup, size_bytes=_size_of(source_path))

        logger.info(
            f"Starting repo index job {job_id}: {len(file_paths)} files from {repo_path}"
        )
        works = [make_work(Path(fp)) for fp in file_paths]
        self._process_bulk_job(job_id, works, collection_id, uploaded_by=uploaded_by)

    def _make_file_progress_callback(
        self,
        job_id: int,
        filename: str,
        idx: int,
        total_files: int,
        write_job: Callable[..., None],
    ):
        """Per-file phase-weighted progress callback for the single-file path."""
        phase_weights = {"extracting": 0.2, "chunking": 0.1, "embedding": 0.6, "saving": 0.1}
        phase_starts = {"extracting": 0, "chunking": 0.2, "embedding": 0.3, "saving": 0.9}

        def progress_callback(
            phase: str,
            progress: int,
            detail: str = None,
            chunks_done: int = 0,
            chunks_total: int = 0,
        ):
            file_progress = phase_starts.get(phase, 0) + (
                phase_weights.get(phase, 0) * progress / 100
            )
            overall = ((idx - 1) + file_progress) / total_files * 100

            write_job(
                phase=phase,
                phase_progress=progress,
                phase_detail=detail,
                chunks_processed=chunks_done,
                chunks_total=chunks_total,
            )
            self._broadcast_event(ProgressEvent(
                job_id=job_id,
                event_type="phase_progress",
                phase=phase,
                current_file=filename,
                file_index=idx,
                total_files=total_files,
                phase_progress=progress,
                phase_detail=detail,
                chunks_processed=chunks_done,
                chunks_total=chunks_total,
                overall_percent=overall,
            ))

        return progress_callback

    def _process_bulk_job(self, job_id: int, works: List[_BulkFileWork], collection_id: str,
                          uploaded_by: Optional[str] = None):
        """Shared bulk pipeline behind local-index and repo-index jobs.

        Extraction/chunking runs in a small worker pool while chunks accumulate
        across files; each flush embeds one large batch (so EMBED_BATCH_SIZE
        batches actually fill, the encode lock still taken per internal batch)
        and persists chunk rows, BM25 entries, FAISS vectors, and document rows
        as single batched writes. Files are consumed and reported strictly in
        submission order, so per-file progress events and the processed_files
        counter behave like the old one-file-at-a-time loop.
        """
        results = {
            "documents_processed": 0,
            "total_pages": 0,
            "total_chunks": 0,
            "document_ids": [],
            "failed_files": [],
        }
        total_files = len(works)
        resolved = 0  # files fully persisted or failed
        # SSE events stay per-file, but the upload_jobs row (read by the
        # polling endpoint) only needs a few writes per second. Throttled
        # updates are merged rather than dropped: dropping them outright
        # meant a job whose last few writes all landed inside one window
        # kept reporting a file it had long since finished.
        last_db_write = 0.0
        deferred: Dict[str, Any] = {}

        def write_job(force: bool = False, **fields):
            nonlocal last_db_write
            deferred.update(fields)
            now = time.monotonic()
            if force or now - last_db_write >= 0.5:
                app_db.update_upload_job(job_id, **deferred)
                deferred.clear()
                last_db_write = now

        try:
            app_db.update_upload_job(job_id, status="running")
            indexer = indexer_manager.get_indexer(collection_id)

            def fail_file(idx: int, work: _BulkFileWork, error: Exception):
                nonlocal resolved
                resolved += 1
                storage_quota.release(collection_id, work.held_bytes)
                work.held_bytes = 0
                logger.error(f"Failed to index {work.display_name}: {error}")
                results["failed_files"].append({
                    "filename": work.display_name,
                    "error": str(error),
                })
                if work.cleanup:
                    try:
                        work.cleanup()
                    except Exception as cleanup_error:
                        logger.warning(
                            f"Cleanup failed for {work.display_name}: {cleanup_error}"
                        )
                write_job(processed_files=resolved)
                self._broadcast_event(ProgressEvent(
                    job_id=job_id,
                    event_type="file_error",
                    phase=UploadPhase.FAILED.value,
                    current_file=work.display_name,
                    file_index=idx,
                    total_files=total_files,
                    error=str(error),
                ))

            def complete_file(idx: int, work: _BulkFileWork, doc_metadata):
                nonlocal resolved
                # The row is committed: it now counts through SUM(file_size).
                storage_quota.release(collection_id, work.held_bytes)
                work.held_bytes = 0
                try:
                    if work.finalize:
                        work.finalize(indexer, doc_metadata)
                    collection_service.add_document(collection_id, doc_metadata.document_id)
                except Exception as e:
                    fail_file(idx, work, e)
                    return
                resolved += 1
                results["documents_processed"] += 1
                results["total_pages"] += doc_metadata.total_pages
                results["total_chunks"] += doc_metadata.total_chunks
                results["document_ids"].append(doc_metadata.document_id)
                write_job(processed_files=resolved, phase=UploadPhase.COMPLETED.value)
                self._broadcast_event(ProgressEvent(
                    job_id=job_id,
                    event_type="file_complete",
                    phase=UploadPhase.COMPLETED.value,
                    current_file=work.display_name,
                    file_index=idx,
                    total_files=total_files,
                    overall_percent=(resolved / total_files) * 100,
                    chunks_total=doc_metadata.total_chunks,
                ))
                logger.info(
                    f"Indexed {work.display_name}: {doc_metadata.total_pages} pages, "
                    f"{doc_metadata.total_chunks} chunks"
                )

            def discard(entries):
                """Give back the staged copies of files that will not be indexed."""
                for entry in entries:
                    work = entry[1]
                    storage_quota.release(collection_id, work.held_bytes)
                    work.held_bytes = 0
                    if work.cleanup:
                        try:
                            work.cleanup()
                        except Exception as cleanup_error:
                            logger.warning(
                                f"Cleanup failed for {work.display_name}: {cleanup_error}"
                            )

            def finish_cancelled():
                """Close the job out as cancelled. Indexed files are kept."""
                if results["documents_processed"] > 0:
                    # Keep the on-disk FAISS index consistent with the chunk
                    # rows already committed to SQLite.
                    indexer.save_index()
                logger.info(
                    f"Job {job_id} cancelled by user after "
                    f"{results['documents_processed']}/{total_files} files"
                )
                app_db.update_upload_job(
                    job_id,
                    status="cancelled",
                    processed_files=resolved,
                    current_file=None,
                    error=(
                        f"Cancelled after indexing {results['documents_processed']} "
                        f"of {total_files} file(s)"
                    ),
                    result_summary=json.dumps(results),
                )
                self._broadcast_event(ProgressEvent(
                    job_id=job_id,
                    event_type="job_cancelled",
                    phase="cancelled",
                    error="Cancelled by user",
                ))

            def persist_batch(batch):
                """Embed + persist a list of (idx, work, prepared) in one pass."""
                if not batch:
                    return
                if self._is_cancelled(job_id):
                    # Embedding a batch is the long pole and cannot be
                    # interrupted once started, so the check belongs here:
                    # refusing to start another batch is what makes Cancel
                    # take effect on a job whose files all extracted early.
                    discard(batch)
                    return
                chunks_total = sum(len(p.chunks) for _, _, p in batch)
                resolved_before = resolved
                detail = (
                    f"Embedding {chunks_total} chunks from {len(batch)} files"
                    if len(batch) > 1 else f"Embedding {chunks_total} chunks"
                )
                last_idx, last_work, _ = batch[-1]

                def embed_progress(done: int, total: int):
                    percent = min(100, int(done / total * 100)) if total else 100
                    write_job(
                        phase=UploadPhase.EMBEDDING.value,
                        phase_progress=percent,
                        phase_detail=detail,
                        chunks_processed=done,
                        chunks_total=total,
                    )
                    self._broadcast_event(ProgressEvent(
                        job_id=job_id,
                        event_type="phase_progress",
                        phase=UploadPhase.EMBEDDING.value,
                        current_file=last_work.display_name,
                        file_index=last_idx,
                        total_files=total_files,
                        phase_progress=percent,
                        phase_detail=detail,
                        chunks_processed=done,
                        chunks_total=total,
                        overall_percent=(
                            (resolved_before + (done / total) * len(batch))
                            / total_files * 100
                        ) if total else 0,
                    ))

                try:
                    metas = indexer.index_prepared_documents(
                        [p for _, _, p in batch],
                        progress_callback=embed_progress,
                    )
                except Exception as e:
                    # One poisoned file must not sink its whole batch: retry
                    # each file alone so only the real culprit(s) fail.
                    # Re-persisting a chunk_id is idempotent (INSERT OR
                    # REPLACE + stale-vector eviction), so a partially
                    # persisted batch is safe to retry.
                    logger.warning(
                        f"Bulk batch of {len(batch)} files failed ({e}); retrying per file"
                    )
                    for idx, work, prep in batch:
                        try:
                            meta = indexer.index_prepared_documents([prep])[0]
                            complete_file(idx, work, meta)
                        except Exception as file_error:
                            fail_file(idx, work, file_error)
                    return

                for (idx, work, _), meta in zip(batch, metas):
                    complete_file(idx, work, meta)

            batcher = ChunkBatcher(settings.bulk_flush_chunks)
            max_workers = max(1, settings.bulk_extract_workers)
            executor = ThreadPoolExecutor(
                max_workers=max_workers,
                thread_name_prefix=f"extract-job-{job_id}",
            )

            def stage_and_prepare(work: _BulkFileWork):
                # Storage cap first: a refused file is never copied or read.
                work.held_bytes = storage_quota.take(
                    collection_id, work.size_bytes, work.display_name
                )
                index_path = work.stage()
                return index_path, indexer.prepare_document(
                    index_path, index_path.name,
                    collection_id=collection_id, uploaded_by=uploaded_by,
                )

            work_iter = iter(enumerate(works, 1))
            pending = deque()

            def submit_next():
                try:
                    idx, work = next(work_iter)
                except StopIteration:
                    return
                pending.append((idx, work, executor.submit(stage_and_prepare, work)))

            try:
                # Bounded lookahead: a 75k-file job never holds more than a
                # window of extracted chunks waiting in futures.
                for _ in range(max_workers * 2):
                    submit_next()

                while pending:
                    if self._is_cancelled(job_id):
                        # Staged-but-never-indexed files (in-flight futures and
                        # the unflushed accumulator) get their cleanup hook so
                        # cancelled jobs don't leave stray library copies.
                        executor.shutdown(wait=False, cancel_futures=True)
                        staged = []
                        for idx, work, future in pending:
                            try:
                                future.result(timeout=30)
                            except Exception:
                                continue  # never staged; nothing to clean up
                            staged.append((idx, work, None))
                        discard(staged + batcher.drain())
                        finish_cancelled()
                        return

                    idx, work, future = pending.popleft()
                    submit_next()

                    self._broadcast_event(ProgressEvent(
                        job_id=job_id,
                        event_type="file_start",
                        phase=UploadPhase.EXTRACTING.value,
                        current_file=work.display_name,
                        file_index=idx,
                        total_files=total_files,
                        overall_percent=(resolved / total_files) * 100,
                    ))
                    write_job(
                        current_file=work.display_name,
                        processed_files=resolved,
                        phase=UploadPhase.EXTRACTING.value,
                    )
                    logger.info(f"Indexing ({idx}/{total_files}): {work.display_name}")

                    try:
                        index_path, prepared = future.result()
                    except Exception as e:
                        fail_file(idx, work, e)
                        continue

                    if prepared is None:
                        # Tabular file (structured-only path): flush what's
                        # pending first so completion events stay ordered,
                        # then index it through the single-file path.
                        persist_batch(batcher.drain())
                        try:
                            doc_metadata = indexer.index_document_with_progress(
                                index_path,
                                index_path.name,
                                progress_callback=self._make_file_progress_callback(
                                    job_id, work.display_name, idx, total_files, write_job
                                ),
                                collection_id=collection_id,
                                uploaded_by=uploaded_by,
                            )
                        except Exception as e:
                            fail_file(idx, work, e)
                            continue
                        complete_file(idx, work, doc_metadata)
                        continue

                    persist_batch(batcher.add((idx, work, prepared), len(prepared.chunks)))

                persist_batch(batcher.drain())
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

            if self._is_cancelled(job_id):
                # Extraction can finish well ahead of embedding, so a job
                # cancelled late lands here rather than in the loop above.
                finish_cancelled()
                return

            if results["documents_processed"] > 0:
                indexer.save_index()

            app_db.update_upload_job(
                job_id,
                status="completed",
                processed_files=total_files,
                current_file=None,
                phase=UploadPhase.COMPLETED.value,
                result_summary=json.dumps(results),
            )
            self._broadcast_event(ProgressEvent(
                job_id=job_id,
                event_type="job_complete",
                phase=UploadPhase.COMPLETED.value,
                total_files=total_files,
                overall_percent=100.0,
            ))
            logger.info(
                f"Bulk index job {job_id} completed: "
                f"{results['documents_processed']}/{total_files} files"
            )

        except Exception as e:
            logger.error(f"Bulk index job {job_id} failed: {e}")
            # Best effort: keep the on-disk FAISS index consistent with the
            # chunk rows already committed to SQLite before the crash.
            if results["documents_processed"] > 0:
                try:
                    indexer.save_index()
                except Exception as save_error:
                    logger.error(f"Could not save index after job failure: {save_error}")
            app_db.update_upload_job(
                job_id,
                status="failed",
                phase=UploadPhase.FAILED.value,
                error=str(e),
                result_summary=json.dumps(results),
            )
            self._broadcast_event(ProgressEvent(
                job_id=job_id,
                event_type="job_error",
                phase=UploadPhase.FAILED.value,
                error=str(e),
            ))

        finally:
            # Files staged but never committed (cancellation, or a crash
            # mid-batch) still hold bytes against the collection's cap.
            # Nothing else gives them back, and a leaked reservation makes
            # the collection look full until the process restarts.
            for work in works:
                if work.held_bytes:
                    storage_quota.release(collection_id, work.held_bytes)
                    work.held_bytes = 0
            # The job's thread slot is released by _run_job, which owns it
            # for the whole run including the parts outside this pipeline.


# Global instance
upload_service = UploadService()
