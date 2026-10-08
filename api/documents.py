"""Document upload, indexing, and management endpoints."""

import logging
import json
import asyncio
import time
from typing import List, Optional
from pathlib import Path
import shutil

from fastapi import HTTPException, Response, UploadFile, File, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import config
from services.document_extractor import DocumentExtractor
from services.collection_service import collection_service
from services.indexer_manager import indexer_manager
from models.schemas import (
    UploadResponse,
    UploadJobResponse,
    DocumentListResponse,
    DocumentMetadata,
    DocumentChunksResponse,
    DocumentChunkView,
    RepoUploadRequest,
    RepoUploadResponse,
    SyncFolderRequest,
    SyncFolderResponse,
    SyncFolderRecord,
    SyncFoldersResponse,
)
from services.upload_service import upload_service
from services.form_field_extractor import (
    extract_form_fields,
    estimate_form_likelihood,
    is_form_like_text,
)
from services import audit, governance, storage_quota
from services.content_policy import BlockedContentError, ContentRejectedError
from services.storage_quota import StorageLimitExceeded
from services.link_fetcher import LinkError, LinkRefused, link_indexing_available, validate_link

from fastapi import APIRouter, Request
from api.deps import (
    get_indexer,
    require_collection_access,
)
from middleware.user_context import get_request_user

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_write(collection_id: str) -> None:
    """Reject callers whose access to the collection is read-only (or absent).

    Read access is enforced by get_indexer for every endpoint; the mutating
    endpoints (upload, index, delete) additionally require write access.
    """
    require_collection_access(collection_id, get_request_user(), write=True)


def _require_ingest(collection_id: str) -> None:
    """Write access plus the acceptable-use acknowledgement.

    Every path that adds sources — upload, staged upload, repo index, local
    index — comes through here, so the AUP gate has exactly one place to
    live. Deletion deliberately does not: refusing the policy must not
    trap someone's existing documents in the index.
    """
    _require_write(collection_id)
    governance.require_aup(get_request_user())


def _is_policy_refusal(error: Exception) -> bool:
    return isinstance(error, (BlockedContentError, ContentRejectedError))


def _upload_size(file: UploadFile) -> int:
    """Bytes of an upload before it is written anywhere.

    Starlette records the size once the body is spooled; a streamed body
    can leave it None, so fall back to seeking the spool.
    """
    size = getattr(file, "size", None)
    if isinstance(size, int) and size >= 0:
        return size
    try:
        spool = file.file
        pos = spool.tell()
        spool.seek(0, 2)
        end = spool.tell()
        spool.seek(pos)
        return max(0, end - pos)
    except Exception:
        return 0


def _content_disposition(disposition: str, filename: str) -> str:
    """A header value that survives any filename.

    HTTP headers are Latin-1; a filename with an em dash or accented letter
    (every page named after its <title>, plenty of uploads) used to make the
    response fail with a 500. The ASCII form is the fallback older clients
    read; filename* carries the real name per RFC 5987.
    """
    from urllib.parse import quote
    ascii_name = filename.encode("ascii", "ignore").decode("ascii").replace('"', "").replace("\\", "")
    ascii_name = ascii_name.strip() or "document"
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def _storage_error(error: StorageLimitExceeded) -> HTTPException:
    """The per-collection cap answers 413 with the numbers the UI needs."""
    return HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=error.to_detail())


@router.post(
    "/documents/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and index documents (PDF, TXT, DOCX, CSV)",
    tags=["documents"],
)
def upload_documents(  # sync: extraction+embedding run in FastAPI's threadpool, not on the event loop
    files: List[UploadFile] = File(...),
    collection_id: str = "default",
) -> UploadResponse:
    """
    Upload one or more documents and automatically index their contents.

    Supported formats: PDF, TXT, DOCX, CSV

    Args:
        files: List of files to upload
        collection_id: Collection to add documents to (default: "default")

    Returns metadata about the indexed documents including page and chunk counts.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files provided",
        )

    # Get indexer for the collection
    _require_ingest(collection_id)
    try:
        indexer = get_indexer(collection_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )

    # Get document directory for this collection
    document_dir = indexer_manager.get_documents_path(collection_id)

    # Validate all files have supported extensions and safe names
    SUPPORTED_EXTENSIONS = DocumentExtractor.SUPPORTED_EXTENSIONS
    for file in files:
        if not file.filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Every uploaded file must have a filename",
            )
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in SUPPORTED_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File {file.filename} has unsupported type. Supported: PDF, TXT, DOCX, CSV, XLSX, XLS, MD, JSON, JSONL, images (PNG/JPG/WEBP/TIFF), audio, and source code files",
            )

    indexed_docs = []
    failed_docs = []
    policy_refusals = 0
    storage_refusals = 0
    total_pages = 0
    total_chunks = 0
    uploaded_by = get_request_user()

    for file in files:
        file_path = None  # per-iteration: the except block must never see a previous file's path
        try:
          # The cap is checked before a single byte lands in the collection,
          # and the reservation holds until the row is committed so parallel
          # uploads into one collection cannot jointly overshoot it.
          with storage_quota.reserve(collection_id, _upload_size(file), file.filename):
            # Save uploaded file to collection's document directory
            # Preserve relative path context by replacing separators with underscores
            # This handles folder uploads where file.filename may be "src/utils/helper.py"
            # Converting to "src_utils_helper.py" prevents collisions and preserves context
            raw_filename = file.filename.replace('\\', '/').lstrip('/')
            if '/' in raw_filename:
                # Folder upload - flatten path to safe filename
                safe_filename = raw_filename.replace('/', '_')
            else:
                # Single file upload - use as-is
                safe_filename = raw_filename
            # Neutralize path traversal / reserved names that survive flattening
            if safe_filename in ("..", ".") or safe_filename.startswith(".."):
                safe_filename = safe_filename.replace("..", "_")
            file_path = document_dir / safe_filename
            with open(file_path, "wb") as f:
                shutil.copyfileobj(file.file, f)

            logger.info(f"Saved uploaded file: {safe_filename} to collection {collection_id}")

            # Index the document
            doc_metadata = indexer.index_document(
                file_path, safe_filename,
                collection_id=collection_id, uploaded_by=uploaded_by,
            )

            # Register document with collection
            collection_service.add_document(collection_id, doc_metadata.document_id)

            indexed_docs.append(doc_metadata.document_id)
            total_pages += doc_metadata.total_pages
            total_chunks += doc_metadata.total_chunks

        except Exception as e:
            if isinstance(e, StorageLimitExceeded):
                storage_refusals += 1
                logger.warning(f"Refused {file.filename}: {e}")
            elif _is_policy_refusal(e):
                policy_refusals += 1
                logger.warning(f"Refused {file.filename}: {e}")
            else:
                logger.error(f"Failed to index {file.filename}: {e}")
            failed_docs.append({"filename": file.filename, "error": str(e)})
            # Clean up this file's partial save if indexing failed
            if file_path is not None:
                try:
                    file_path.unlink(missing_ok=True)
                except Exception as cleanup_err:
                    logger.warning(f"Could not remove partial upload {file_path}: {cleanup_err}")
            # Continue processing remaining files

    # Persist the index if any documents were successfully indexed
    if indexed_docs:
        indexer.save_index()
        audit.record("document.upload", collection_id=collection_id,
                     detail={"files": [f.filename for f in files][:50],
                             "document_ids": indexed_docs[:50],
                             "indexed": len(indexed_docs), "failed": len(failed_docs)})

    # Build response message
    if failed_docs and not indexed_docs:
        # All files failed. A policy or storage refusal is the caller's
        # problem, not the server's, so it reports as 422/413 rather than 500.
        error_details = "; ".join([f"{f['filename']}: {f['error']}" for f in failed_docs])
        if storage_refusals == len(failed_docs):
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=error_details,
            )
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
                if policy_refusals + storage_refusals == len(failed_docs)
                else status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=f"All files failed to index. Errors: {error_details}",
        )

    if failed_docs:
        # Partial success
        failed_names = [f["filename"] for f in failed_docs]
        message = f"Indexed {len(indexed_docs)} document(s) to collection '{collection_id}'. Failed: {', '.join(failed_names)}"
    else:
        # Full success
        message = f"Successfully indexed {len(indexed_docs)} document(s) to collection '{collection_id}'"

    return UploadResponse(
        message=message,
        documents_processed=len(indexed_docs),
        total_pages=total_pages,
        total_chunks=total_chunks,
        document_ids=indexed_docs,
    )


@router.post(
    "/documents/upload-staged",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Stage uploads for background indexing (save only, no indexing)",
    tags=["documents"],
)
def stage_uploads(  # sync: disk writes run in FastAPI's threadpool
    files: List[UploadFile] = File(...),
    collection_id: str = "default",
) -> dict:
    """
    Save uploaded files into the collection's documents directory WITHOUT
    indexing them, and return the stored paths.

    This is the transfer half of background uploads: the browser streams
    batches here (fast — no embedding in the request), then submits ONE
    /documents/index-local-async job over the returned paths. The browser
    only has to stay open for the transfer; indexing continues server-side.

    Filename handling matches /documents/upload (folder paths flattened,
    traversal neutralized); a re-staged filename overwrites the previous
    staged copy, mirroring the sync endpoint.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files provided",
        )
    _require_ingest(collection_id)
    try:
        get_indexer(collection_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    document_dir = indexer_manager.get_documents_path(collection_id)
    SUPPORTED_EXTENSIONS = DocumentExtractor.SUPPORTED_EXTENSIONS

    # Staging is the transfer half of a background upload: refuse the batch
    # before writing it rather than accepting files the index job would then
    # have to reject one by one (that job re-checks per file regardless).
    try:
        storage_quota.check(collection_id, sum(_upload_size(f) for f in files))
    except StorageLimitExceeded as e:
        raise _storage_error(e)

    staged = []
    failed = []
    for file in files:
        if not file.filename:
            failed.append({"filename": "", "error": "missing filename"})
            continue
        if Path(file.filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
            failed.append({"filename": file.filename, "error": "unsupported type"})
            continue
        raw_filename = file.filename.replace('\\', '/').lstrip('/')
        safe_filename = raw_filename.replace('/', '_') if '/' in raw_filename else raw_filename
        if safe_filename in ("..", ".") or safe_filename.startswith(".."):
            safe_filename = safe_filename.replace("..", "_")
        file_path = document_dir / safe_filename
        try:
            with open(file_path, "wb") as f:
                shutil.copyfileobj(file.file, f)
            staged.append({"filename": safe_filename, "path": str(file_path)})
        except Exception as e:
            logger.error(f"Failed to stage {file.filename}: {e}")
            failed.append({"filename": file.filename, "error": str(e)})

    return {"staged": staged, "failed": failed, "collection_id": collection_id}


def _job_row_to_response(job: dict, with_summary: bool = False) -> UploadJobResponse:
    """One shape for a job row, wherever it is listed.

    The active-jobs list used to build its own dict and leave out job_type,
    so every restored local-index job came back after a page refresh
    labelled "Upload".
    """
    total = job.get("total_files") or 0
    processed = job.get("processed_files") or 0
    status_value = job.get("status")
    if status_value == "completed":
        progress = 100.0
    elif total > 0:
        progress = round((processed / total) * 100, 1)
    else:
        progress = 0.0

    summary = None
    if with_summary and job.get("result_summary"):
        try:
            summary = json.loads(job["result_summary"])
        except (json.JSONDecodeError, TypeError):
            summary = None

    return UploadJobResponse(
        job_id=job["id"],
        collection_id=job["collection_id"],
        status=status_value,
        total_files=total,
        processed_files=processed,
        current_file=job.get("current_file"),
        progress_percent=progress,
        error=job.get("error"),
        result_summary=summary,
        started_at=job.get("started_at"),
        completed_at=job.get("completed_at"),
        phase=job.get("phase"),
        phase_progress=job.get("phase_progress"),
        phase_detail=job.get("phase_detail"),
        chunks_processed=job.get("chunks_processed"),
        chunks_total=job.get("chunks_total"),
        job_type=job.get("job_type") or "upload",
        queue_position=upload_service.queue_position(job["id"]),
        cancel_requested=upload_service.cancel_requested(job["id"]),
    )


@router.get(
    "/documents/upload/{job_id}/status",
    response_model=UploadJobResponse,
    summary="Get upload job status",
    tags=["documents"],
)
async def get_upload_status(job_id: int) -> UploadJobResponse:
    """
    Get the status of a background upload job.

    Poll this endpoint every 1-2 seconds while status is 'pending' or 'running'.

    Args:
        job_id: The job ID returned from /documents/upload-async

    Returns:
        Current job status including progress percentage
    """
    job_status = upload_service.get_job_status(job_id)

    if not job_status:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Upload job {job_id} not found",
        )

    return UploadJobResponse(
        job_id=job_status["job_id"],
        collection_id=job_status["collection_id"],
        status=job_status["status"],
        total_files=job_status["total_files"],
        processed_files=job_status["processed_files"],
        current_file=job_status["current_file"],
        progress_percent=job_status["progress_percent"],
        error=job_status["error"],
        result_summary=job_status["result_summary"],
        started_at=job_status["started_at"],
        completed_at=job_status["completed_at"],
        # v4.0: Granular progress
        phase=job_status.get("phase"),
        phase_progress=job_status.get("phase_progress"),
        phase_detail=job_status.get("phase_detail"),
        chunks_processed=job_status.get("chunks_processed"),
        chunks_total=job_status.get("chunks_total"),
        job_type=job_status.get("job_type", "upload"),
        queue_position=job_status.get("queue_position"),
        cancel_requested=job_status.get("cancel_requested", False),
    )


@router.get(
    "/documents/upload/active",
    response_model=List[UploadJobResponse],
    summary="Get all active upload jobs",
    tags=["documents"],
)
async def get_active_upload_jobs() -> List[UploadJobResponse]:
    """
    Get all active (pending/running) upload jobs.

    Use this endpoint on page load to restore the notification bell state.

    Returns:
        List of active upload jobs
    """
    from services.app_database import app_db

    return [_job_row_to_response(job) for job in app_db.get_all_active_upload_jobs()]


@router.get(
    "/documents/jobs",
    response_model=List[UploadJobResponse],
    summary="Recent indexing jobs, finished ones included",
    tags=["documents"],
)
async def list_recent_jobs(limit: int = 20, collection_id: Optional[str] = None) -> List[UploadJobResponse]:
    """
    Recent indexing jobs in every state, newest first.

    The active-jobs endpoint only restores work in flight. This one is what
    lets a person find out, after a refresh or a day later, that four files
    in last night's folder add never made it in - the per-file failures ride
    along in each job's result_summary.

    Args:
        limit: How many jobs to return (1-200, default 20)
        collection_id: Restrict to one collection

    Returns:
        Jobs newest first, each with its result summary parsed
    """
    from services.app_database import app_db

    jobs = app_db.get_recent_upload_jobs(limit=limit, collection_id=collection_id)
    return [_job_row_to_response(job, with_summary=True) for job in jobs]


@router.get(
    "/documents/upload/{job_id}/stream",
    summary="Stream real-time upload progress via SSE",
    tags=["documents"],
)
async def stream_upload_progress(job_id: int):
    """
    Stream real-time progress updates for an upload job via Server-Sent Events (SSE).

    This endpoint provides instant updates without polling. Connect using EventSource
    in JavaScript to receive progress events as they happen.

    Event types:
    - file_start: A new file is starting to process
    - phase_progress: Progress within a phase (extracting, chunking, embedding, saving)
    - file_complete: A file finished processing
    - file_error: A file failed to process
    - job_complete: All files processed successfully
    - job_error: Job failed with error
    - job_cancelled: Job was cancelled by user

    Example JavaScript:
    ```javascript
    const eventSource = new EventSource(`/documents/upload/${jobId}/stream`);
    eventSource.addEventListener('phase_progress', (e) => {
        const data = JSON.parse(e.data);
        console.log(`${data.phase}: ${data.phase_progress}%`);
    });
    eventSource.addEventListener('job_complete', () => {
        eventSource.close();
    });
    ```

    Args:
        job_id: The job ID returned from /documents/upload-async

    Returns:
        SSE stream of progress events
    """
    import queue

    # Check job exists
    job_status = upload_service.get_job_status(job_id)
    if not job_status:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Upload job {job_id} not found",
        )

    # Subscribe to events
    event_queue = upload_service.subscribe_to_events(job_id)

    async def event_generator():
        """Generate SSE events from the queue."""
        try:
            # Send initial status
            initial_data = {
                "job_id": job_id,
                "event_type": "connected",
                "phase": job_status.get("phase") or "pending",
                "overall_percent": job_status.get("progress_percent", 0),
                "current_file": job_status.get("current_file"),
                "file_index": job_status.get("processed_files", 0),
                "total_files": job_status.get("total_files", 0),
            }
            yield f"event: connected\ndata: {json.dumps(initial_data)}\n\n"

            # If job is already complete, send final event and close
            if job_status["status"] in ("completed", "failed", "cancelled"):
                final_data = {
                    "job_id": job_id,
                    "event_type": f"job_{job_status['status']}",
                    "phase": job_status["status"],
                }
                yield f"event: job_{job_status['status']}\ndata: {json.dumps(final_data)}\n\n"
                return

            # Stream events from the queue.
            #
            # Polled without blocking rather than waited on in a worker
            # thread: the old version parked a threadpool thread for up to
            # 30 seconds per open stream, so a handful of browsers watching
            # jobs could starve the pool that also serves uploads.
            idle_since = time.monotonic()
            while True:
                try:
                    event = event_queue.get_nowait()
                except queue.Empty:
                    if time.monotonic() - idle_since >= 20:
                        idle_since = time.monotonic()
                        yield ": keepalive\n\n"
                    await asyncio.sleep(0.25)
                    continue

                idle_since = time.monotonic()
                yield event.to_sse()

                # Check for terminal events
                if event.event_type in ("job_complete", "job_error", "job_cancelled"):
                    break

        except asyncio.CancelledError:
            # Client disconnected
            pass
        finally:
            # Unsubscribe from events
            upload_service.unsubscribe_from_events(job_id, event_queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@router.post(
    "/documents/upload/{job_id}/cancel",
    summary="Cancel a running upload job",
    tags=["documents"],
)
async def cancel_upload_job(job_id: int):
    """
    Request cancellation of a running upload job.

    The job will be marked as cancelled after the current file finishes processing.
    Already-indexed files will remain in the collection.

    Args:
        job_id: The job ID to cancel

    Returns:
        Cancellation status
    """
    # Check job exists and is active
    job_status = upload_service.get_job_status(job_id)
    if not job_status:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Upload job {job_id} not found",
        )

    if job_status["status"] not in ("pending", "running"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job {job_id} is not active (status: {job_status['status']})",
        )

    # A queued job is dropped outright; a running one is asked to stop and
    # does so at the next file boundary; anything else is an orphan row.
    outcome = upload_service.cancel_job(job_id)

    if outcome == "cancelled":
        return {
            "message": f"Job {job_id} cancelled before it started",
            "job_id": job_id,
            "status": "cancelled",
        }

    if outcome == "cancelling":
        return {
            "message": "Stopping after the file being indexed right now",
            "job_id": job_id,
            "status": "cancelling",
        }

    # Thread not in active list - try force cancellation for orphaned jobs
    logger.warning(f"Job {job_id} thread not found, attempting force cancellation")
    if upload_service.cancel_job(job_id, force=True) == "cancelled":
        return {
            "message": f"Job {job_id} force-cancelled (thread was not active)",
            "job_id": job_id,
            "status": "cancelled",
        }

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Failed to cancel job {job_id}",
    )


@router.post(
    "/documents/upload-repo",
    response_model=RepoUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload and index a code repository or folder (async)",
    tags=["documents"],
)
def upload_repository(  # sync: extraction+embedding run in FastAPI's threadpool, not on the event loop
    request: RepoUploadRequest,
    response: Response,
) -> RepoUploadResponse:
    """
    Scan a local repository or folder and start indexing in the background.

    This endpoint returns immediately with a job_id. Use GET /upload-jobs/{job_id}
    to track progress.

    Indexing is incremental: files whose exact bytes are already in the
    collection are skipped (``skipped_unchanged``). When every file is
    unchanged no job is created and the response is 200 with
    ``status: "up_to_date"`` and ``job_id: null``.

    **Supported file types:**
    - Documents: PDF, TXT, DOCX, CSV, MD, JSON
    - Code: Python, JavaScript, TypeScript, C#, Java, Go, Rust, C/C++, PHP, Ruby, Swift, Kotlin, Scala
    - Legacy: Pascal/Delphi, Modula-2, Assembly

    **Code-aware chunking:**
    Code files are intelligently chunked to preserve symbol boundaries
    (procedures, functions, classes, records, etc.) for better RAG performance.

    Args:
        request: Repository upload configuration including:
            - path: Local filesystem path to scan
            - collection_id: Target collection (default: "default")
            - recursive: Whether to scan subdirectories (default: True)
            - file_extensions: List of extensions to include (optional)
            - exclude_patterns: Glob patterns to exclude

    Returns:
        Response with job_id for tracking progress
    """
    repo_path = Path(request.path)

    if not repo_path.exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path does not exist: {request.path}",
        )

    if not repo_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path is not a directory: {request.path}",
        )

    # Verify collection exists and the caller can write to it
    _require_ingest(request.collection_id)
    try:
        get_indexer(request.collection_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )

    # Build file extensions list from include_patterns or use defaults
    file_extensions = None
    if request.include_patterns:
        # Convert glob patterns like "*.py" to extensions like ".py"
        file_extensions = []
        for pattern in request.include_patterns:
            if pattern.startswith("*."):
                file_extensions.append("." + pattern[2:].lower())
    elif hasattr(request, 'file_extensions') and request.file_extensions:
        file_extensions = request.file_extensions

    try:
        # Start background job
        job_id, files_found, skipped_unchanged = upload_service.start_repo_index(
            repo_path=request.path,
            collection_id=request.collection_id,
            recursive=request.recursive,
            file_extensions=file_extensions,
            exclude_patterns=request.exclude_patterns,
            uploaded_by=get_request_user(),
        )

        if job_id is None:
            # Every file is already in the collection: nothing to wait for,
            # so no 202 and no job to poll.
            response.status_code = status.HTTP_200_OK
            return RepoUploadResponse(
                message=(
                    f"Already up to date: all {skipped_unchanged} file(s) are in the collection."
                ),
                status="up_to_date",
                job_id=None,
                files_found=files_found,
                skipped_unchanged=skipped_unchanged,
            )

        # The count matters to the caller: the UI used to read files_indexed
        # off this response, get the 0 that was always there, and tell the
        # person their folder had added nothing while the job ran fine.
        to_index = files_found - skipped_unchanged
        unchanged_note = f" ({skipped_unchanged} unchanged, skipped)" if skipped_unchanged else ""
        queued_behind = upload_service.queue_position(job_id)
        if queued_behind:
            message = (
                f"{to_index} file(s) to index{unchanged_note}. Queued behind "
                f"{queued_behind} other job(s); indexing starts automatically."
            )
        else:
            message = f"Indexing {to_index} file(s) in the background{unchanged_note}."

        return RepoUploadResponse(
            message=message,
            status="queued",
            job_id=job_id,
            files_found=files_found,
            skipped_unchanged=skipped_unchanged,
        )

    except ValueError as e:
        # Nothing matched - a dead end for the caller, not a started job.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except RuntimeError as e:
        # The waiting list is full
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )

@router.post(
    "/documents/sync-folder",
    response_model=SyncFolderResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Sync a folder into a collection (incremental, optional prune)",
    tags=["documents"],
)
def sync_folder(  # sync: hashing and pruning run in FastAPI's threadpool, not on the event loop
    request: SyncFolderRequest,
    response: Response,
) -> SyncFolderResponse:
    """
    Bring a collection up to date with a folder on disk.

    Files whose bytes are already indexed are skipped; a file that changed
    since the last sync has its old document replaced; with
    ``prune_missing`` documents whose file has disappeared from the folder
    are removed. Only new and changed files go through a background job.

    Returns 202 with ``status: "queued"`` and a ``job_id`` when something
    was queued, or 200 with ``status: "up_to_date"`` (``job_id: null``)
    when nothing needed indexing — pruning may still have happened, see
    ``pruned``.
    """
    folder = Path(request.path)
    if not folder.exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path does not exist: {request.path}",
        )
    if not folder.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path is not a directory: {request.path}",
        )

    # Same gate as /documents/upload-repo: write access plus the AUP, and
    # the collection must exist. Pruning deletes documents, which the
    # write gate already covers.
    _require_ingest(request.collection_id)
    try:
        get_indexer(request.collection_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    file_extensions = None
    if request.file_extensions:
        file_extensions = [
            e.lower() if e.startswith(".") else "." + e.lower()
            for e in request.file_extensions
        ]

    try:
        outcome = upload_service.sync_folder(
            path=request.path,
            collection_id=request.collection_id,
            recursive=request.recursive,
            file_extensions=file_extensions,
            exclude_patterns=request.exclude_patterns,
            prune_missing=request.prune_missing,
            uploaded_by=get_request_user(),
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except RuntimeError as e:
        # The waiting list is full
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    parts = []
    if outcome["queued"]:
        parts.append(f"indexing {outcome['queued']} new or changed file(s)")
    if outcome["skipped_unchanged"]:
        parts.append(f"{outcome['skipped_unchanged']} unchanged")
    if outcome["replaced_count"]:
        parts.append(f"{outcome['replaced_count']} replaced")
    if outcome["pruned_count"]:
        parts.append(f"{outcome['pruned_count']} removed")
    if outcome["job_id"] is None:
        response.status_code = status.HTTP_200_OK
        message = "Up to date" + (f": {', '.join(parts)}." if parts else ".")
    else:
        message = ", ".join(parts).capitalize() + "."

    return SyncFolderResponse(message=message, **outcome)


@router.get(
    "/documents/sync-folders",
    response_model=SyncFoldersResponse,
    summary="Folders previously synced or indexed into a collection",
    tags=["documents"],
)
def list_sync_folders(collection_id: str = "default") -> SyncFoldersResponse:
    """The folders a collection was synced from, newest first, with the
    options and counts of each one's last run — what a "Sync again"
    button needs. ``exists`` is false for a folder that has since gone."""
    try:
        get_indexer(collection_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    folders = []
    for entry in upload_service.list_sync_folders(collection_id):
        known = {k: v for k, v in entry.items() if k in SyncFolderRecord.model_fields}
        known["exists"] = Path(entry["path"]).is_dir()
        folders.append(SyncFolderRecord(**known))
    return SyncFoldersResponse(collection_id=collection_id, folders=folders)


class IndexLocalAsyncRequest(BaseModel):
    """Request body for async local file indexing."""
    file_paths: List[str]
    collection_id: str = "default"
    copy_to_library: bool = False


@router.post(
    "/documents/index-local-async",
    response_model=UploadJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Index local files in background",
    tags=["documents"],
)
async def index_local_files_async(request: IndexLocalAsyncRequest) -> UploadJobResponse:
    """
    Start a background job to index local files.

    This is useful for indexing multiple files or large files without
    blocking the UI. Progress can be tracked via /documents/upload-status/{job_id}.

    Args:
        request: Contains list of file paths, collection_id, and copy_to_library flag

    Returns:
        UploadJobResponse with job_id for tracking progress
    """

    SUPPORTED_EXTENSIONS = DocumentExtractor.SUPPORTED_EXTENSIONS

    # Sort the batch rather than refusing it. One unreadable path used to
    # abort the whole submission, which on a folder drop of thousands of
    # files meant none of them were indexed and the person was told only
    # about the first bad one.
    valid_paths = []
    skipped: List[dict] = []
    for file_path in request.file_paths:
        path = Path(file_path)
        if not path.exists():
            skipped.append({"filename": path.name or file_path, "error": "file not found"})
            continue
        if not path.is_file():
            skipped.append({"filename": path.name or file_path, "error": "not a file"})
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            skipped.append({
                "filename": path.name,
                "error": f"unsupported file type ({path.suffix.lower() or 'no extension'})",
            })
            continue
        valid_paths.append(file_path)

    if not valid_paths:
        detail = "No files could be indexed"
        if skipped:
            shown = "; ".join(f"{s['filename']}: {s['error']}" for s in skipped[:5])
            more = f" (and {len(skipped) - 5} more)" if len(skipped) > 5 else ""
            detail = f"{detail} - {shown}{more}"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        )

    _require_ingest(request.collection_id)
    try:
        storage_quota.check(
            request.collection_id,
            sum(Path(fp).stat().st_size for fp in valid_paths),
        )
    except StorageLimitExceeded as e:
        raise _storage_error(e)
    try:
        job_id = upload_service.start_local_index(
            file_paths=valid_paths,
            collection_id=request.collection_id,
            copy_to_library=request.copy_to_library,
            uploaded_by=get_request_user(),
        )

        return UploadJobResponse(
            job_id=job_id,
            collection_id=request.collection_id,
            status="pending",
            total_files=len(valid_paths),
            processed_files=0,
            progress_percent=0.0,
            job_type="index",
            queue_position=upload_service.queue_position(job_id),
            skipped_files=skipped,
        )

    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Failed to start local index job: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start indexing: {str(e)}",
        )

class IndexLinksRequest(BaseModel):
    """Request body for indexing the content behind links."""
    urls: List[str]
    collection_id: str = "default"


MAX_LINKS_PER_REQUEST = 50


@router.post(
    "/documents/index-links",
    response_model=UploadJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Fetch and index the content behind links in the background",
    tags=["documents"],
)
async def index_links_async(request: IndexLinksRequest) -> UploadJobResponse:
    """
    Start a background job that fetches each link and indexes what it finds.

    A web page is saved as HTML and sectioned by heading; a link straight
    to a PDF, Office file, text, Markdown, JSON, CSV, or image is saved as
    that file type. Each document keeps the link as its source_path
    (source_type='url'). Progress is tracked like any other index job via
    /documents/upload/{job_id}/status.

    Links are validated before the job starts: malformed URLs and hosts
    that resolve to private or local addresses are listed in skipped_files
    rather than failing the request. The whole request is refused when
    link indexing is unavailable (OFFLINE_MODE or LINK_INDEXING_ENABLED=false).
    """
    if not link_indexing_available():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Link indexing is unavailable in offline mode"
                if config.settings.offline_mode else "Link indexing is disabled on this server"
            ),
        )

    # Blank lines from a pasted list are noise, not errors.
    submitted = [u.strip() for u in request.urls if u and u.strip()]
    if not submitted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No links provided")
    if len(submitted) > MAX_LINKS_PER_REQUEST:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"At most {MAX_LINKS_PER_REQUEST} links per request",
        )

    _require_ingest(request.collection_id)
    try:
        get_indexer(request.collection_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    valid_urls: List[str] = []
    skipped: List[dict] = []
    seen = set()
    # Validation resolves DNS; keep it off the event loop.
    for raw in submitted:
        try:
            url = await asyncio.to_thread(validate_link, raw)
        except (LinkError, LinkRefused) as e:
            skipped.append({"filename": raw, "error": str(e)})
            continue
        if url in seen:
            continue
        seen.add(url)
        valid_urls.append(url)

    if not valid_urls:
        shown = "; ".join(f"{s['filename']}: {s['error']}" for s in skipped[:5])
        more = f" (and {len(skipped) - 5} more)" if len(skipped) > 5 else ""
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No links could be indexed - {shown}{more}",
        )

    try:
        job_id = upload_service.start_link_index(
            urls=valid_urls,
            collection_id=request.collection_id,
            uploaded_by=get_request_user(),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to start link index job: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start indexing: {str(e)}",
        )

    return UploadJobResponse(
        job_id=job_id,
        collection_id=request.collection_id,
        status="pending",
        total_files=len(valid_urls),
        processed_files=0,
        progress_percent=0.0,
        job_type="index",
        queue_position=upload_service.queue_position(job_id),
        skipped_files=skipped,
    )


@router.get(
    "/documents",
    response_model=DocumentListResponse,
    summary="List all indexed documents",
    tags=["documents"],
)
async def list_documents(
    collection_id: str = "default",
    limit: int = 0,
    offset: int = 0,
    q: str = "",
    kind: str = "",
) -> DocumentListResponse:
    """
    List indexed documents with their metadata.

    Args:
        collection_id: Collection to list documents from (default: "default")
        limit: Page size. 0 (the default) returns everything — the
            backwards-compatible behavior for existing callers. Pass a
            positive limit for large collections: pagination happens in SQL
            and the per-document filesystem timestamp fallback is skipped,
            so a page over a 75k-document collection stays fast.
        offset: Page start (only meaningful with limit > 0).
        q: Optional filename substring filter (applies to paged mode; the
            total_documents in the response honors it).

    Returns filename, page count, and chunk count for each document, plus
    total_documents (the full filtered count, not the page size).
    """
    try:
        # Get indexer for the collection
        try:
            indexer = get_indexer(collection_id)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            )

        # Get document directory for this collection
        document_dir = indexer_manager.get_documents_path(collection_id)
        collection = collection_service.get_collection(collection_id)

        # `kind` narrows a paged list to one family (code / docs / data /
        # media / other); the per-kind totals always describe the whole
        # (substring-filtered) set so the chips keep their numbers.
        from services import file_kinds
        kind = (kind or "").strip().lower()
        keys = None
        kind_counts = None
        if kind and kind != "all":
            if kind == "other":
                known = set()
                for k in file_kinds.KINDS:
                    known |= file_kinds.extensions_for_kind(k)
                key_counts = indexer.vector_store.metadata_store.count_documents_by_key(q=q)
                keys = [k for k in key_counts if k not in known]
            elif kind in file_kinds.KINDS:
                keys = file_kinds.keys_for_kind(kind)
            else:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail=f"Unknown kind {kind!r}; use one of {', '.join(file_kinds.KINDS)}, other")

        paged = limit > 0
        if paged:
            documents = indexer.list_documents_page(limit, offset=offset, q=q, keys=keys)
            total_documents = indexer.count_documents(q=q, keys=keys)
            kind_counts = file_kinds.kind_counts(
                indexer.vector_store.metadata_store.count_documents_by_key(q=q)
            )
        else:
            documents = indexer.list_documents()
            if keys is not None:
                wanted = set(keys)
                documents = [d for d in documents
                             if file_kinds.kind_key_for_filename(d.get("filename", "")) in wanted]
            total_documents = len(documents)

        # Convert to DocumentMetadata objects
        doc_metadata_list = []
        for doc in documents:
            # Determine file path based on source type
            # Handle None values from database by defaulting to "upload"
            source_type = doc.get("source_type") or "upload"
            source_path = doc.get("source_path")

            if source_type == "local_reference" and source_path:
                # Local reference - use source_path
                doc_path = Path(source_path)
            else:
                # Uploaded file - use documents directory
                doc_path = document_dir / doc["filename"]

            # Get timestamp from file modification time or upload_timestamp
            # Handle None values by defaulting to empty string. The stat()
            # fallback is skipped in paged mode: one filesystem stat per
            # document is exactly the kind of per-row cost pagination exists
            # to avoid, and rows missing upload_timestamp are legacy-rare.
            indexed_at = doc.get("upload_timestamp") or ""
            if not indexed_at and not paged and doc_path.exists():
                from datetime import datetime
                mtime = doc_path.stat().st_mtime
                indexed_at = datetime.fromtimestamp(mtime).isoformat()

            # Handle both field naming conventions (total_pages/num_pages, total_chunks/num_chunks)
            total_pages = doc.get("total_pages") or doc.get("num_pages") or 0
            total_chunks = doc.get("total_chunks") or doc.get("num_chunks") or 0

            doc_metadata = DocumentMetadata(
                document_id=doc["document_id"],
                filename=doc["filename"],
                total_pages=total_pages,
                total_chunks=total_chunks,
                indexed_at=indexed_at,
                source_type=source_type,
                source_path=source_path,
                source_format=doc.get("source_format"),
                injection_warnings=doc.get("injection_warnings"),
                uploaded_by=doc.get("uploaded_by"),
                content_hash=doc.get("content_hash"),
                sensitivity=doc.get("sensitivity"),
                sensitivity_effective=governance.effective_sensitivity(collection, doc.get("sensitivity")),
                policy_status=doc.get("policy_status") or "clear",
                policy_flags=doc.get("policy_flags"),
            )
            doc_metadata_list.append(doc_metadata)

        return DocumentListResponse(
            documents=doc_metadata_list,
            total_documents=total_documents,
            kind_counts=kind_counts,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list documents: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list documents: {str(e)}",
        )


@router.get(
    "/documents/{document_id}/pdf",
    summary="Download document file",
    tags=["documents"],
    response_class=FileResponse,
)
async def get_pdf(
    document_id: str,
    collection_id: str = "default",
):
    """
    Download the document file for a specific document.

    Args:
        document_id: Document ID
        collection_id: Collection containing the document (default: "default")

    For PDFs, the URL can include #page=N to open at a specific page in the browser.
    """
    try:
        # Get indexer for the collection
        try:
            indexer = get_indexer(collection_id)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            )

        # Get document directory for this collection
        document_dir = indexer_manager.get_documents_path(collection_id)

        # Get document metadata to find filename and source info
        doc_info = indexer.vector_store.metadata_store.get_document_info(document_id)

        if not doc_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document {document_id} not found",
            )
        # Quarantined documents are only served to a reviewer.
        governance.assert_document_servable(doc_info)

        # Determine file path based on source type
        if doc_info.get("source_type") == "local_reference" and doc_info.get("source_path"):
            # Local reference - serve from original location
            doc_path = Path(doc_info["source_path"])
            if not doc_path.exists():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Source file no longer exists at: {doc_info['source_path']}. The file may have been moved or deleted.",
                )
        else:
            # Uploaded file - serve from collection's documents directory
            doc_path = document_dir / doc_info["filename"]
            if not doc_path.exists():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Document file not found: {doc_info['filename']}",
                )

        # Determine media type based on file extension
        file_ext = Path(doc_info["filename"]).suffix.lower()
        media_types = {
            '.pdf': 'application/pdf',
            '.txt': 'text/plain',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.csv': 'text/csv',
            '.md': 'text/markdown',
            '.json': 'application/json',
            # Saved HTML (uploads and fetched links) is shown as source, not
            # rendered: a fetched page's scripts must never run on this origin.
            '.html': 'text/plain; charset=utf-8',
            '.htm': 'text/plain; charset=utf-8',
            # Code files - serve as plain text for browser preview
            '.pas': 'text/plain',
            '.dpr': 'text/plain',
            '.dpk': 'text/plain',
            '.pp': 'text/plain',
            '.inc': 'text/plain',
            '.dfm': 'text/plain',
            '.mod': 'text/plain',
            '.def': 'text/plain',
            '.mi': 'text/plain',
            '.asm': 'text/plain',
            '.s': 'text/plain',
            # Images - browsers preview these natively
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.webp': 'image/webp',
            '.gif': 'image/gif',
            '.bmp': 'image/bmp',
            '.tif': 'image/tiff',
            '.tiff': 'image/tiff',
        }
        media_type = media_types.get(file_ext, 'application/octet-stream')

        # Files that can be previewed inline in the browser
        inline_extensions = {'.pdf', '.txt', '.md', '.json', '.csv', '.html', '.htm',
                            '.pas', '.dpr', '.dpk', '.pp', '.inc', '.dfm',
                            '.mod', '.def', '.mi', '.asm', '.s',
                            '.png', '.jpg', '.jpeg', '.webp', '.gif'}
        disposition = 'inline' if file_ext in inline_extensions else 'attachment'
        return FileResponse(
            path=doc_path,
            media_type=media_type,
            headers={"Content-Disposition": _content_disposition(disposition, doc_info["filename"])},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to retrieve PDF: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve PDF: {str(e)}",
        )


@router.get(
    "/documents/{document_id}/chunks",
    response_model=DocumentChunksResponse,
    summary="List indexed chunks for a document",
    tags=["documents"],
)
async def get_document_chunks(
    document_id: str,
    collection_id: str = "default",
    include_fields: bool = True,
    max_chunks: int = 1000,
) -> DocumentChunksResponse:
    """
    Return indexed chunks for a document, with optional OCR field/value extraction.

    Args:
        document_id: Document ID
        collection_id: Collection containing the document
        include_fields: If true, extract key/value fields for OCR/hybrid chunks
        max_chunks: Maximum number of chunks to return (safety limit)
    """
    try:
        # Get indexer for the collection
        try:
            indexer = get_indexer(collection_id)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            )

        metadata_store = indexer.vector_store.metadata_store
        doc_info = metadata_store.get_document_info(document_id)
        if not doc_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document {document_id} not found",
            )
        governance.assert_document_servable(doc_info)

        all_chunks = metadata_store.get_chunks_by_document(document_id)
        if max_chunks > 0:
            all_chunks = all_chunks[:max_chunks]

        chunk_views: List[DocumentChunkView] = []
        for chunk in all_chunks:
            chunk_method = chunk.get("extraction_method") or doc_info.get("extraction_method")
            extracted_fields = None
            form_score = None
            form_like = None
            field_extraction_applied = None

            if include_fields and chunk_method in {"ocr", "hybrid"}:
                text = chunk.get("text", "")
                form_score = estimate_form_likelihood(text)
                form_like = is_form_like_text(text)
                if form_like:
                    field_extraction_applied = True
                    extracted = extract_form_fields(text)
                    if extracted:
                        extracted_fields = extracted
                else:
                    field_extraction_applied = False

            chunk_views.append(
                DocumentChunkView(
                    chunk_id=chunk["chunk_id"],
                    page_number=chunk["page_number"],
                    chunk_index=chunk["chunk_index"],
                    text=chunk["text"],
                    source_format=chunk.get("source_format"),
                    extraction_method=chunk_method,
                    language=chunk.get("language"),
                    symbol_name=chunk.get("symbol_name"),
                    symbol_type=chunk.get("symbol_type"),
                    line_start=chunk.get("line_start"),
                    line_end=chunk.get("line_end"),
                    extracted_fields=extracted_fields,
                    form_score=round(form_score, 3) if form_score is not None else None,
                    form_like=form_like,
                    field_extraction_applied=field_extraction_applied,
                )
            )

        total_chunks = doc_info.get("num_chunks", len(chunk_views))
        extraction_method = doc_info.get("extraction_method")

        return DocumentChunksResponse(
            document_id=document_id,
            filename=doc_info.get("filename", ""),
            extraction_method=extraction_method,
            total_chunks=total_chunks,
            returned_chunks=len(chunk_views),
            chunks=chunk_views,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list chunks for document {document_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list document chunks: {str(e)}",
        )


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a document from the index",
    tags=["documents"],
)
def delete_document(  # sync: FAISS rebuild on delete runs in the threadpool
    document_id: str,
    collection_id: str = "default",
):
    """
    Remove a document and all its chunks from the index.

    Args:
        document_id: Document ID to delete
        collection_id: Collection containing the document (default: "default")

    This will delete:
    - The document's metadata from the index
    - All chunks associated with the document
    - For uploaded files: the document file from the collection's documents directory
    - For local references: only the index (original file is NOT deleted)
    """
    try:
        # Get indexer for the collection
        try:
            indexer = get_indexer(collection_id)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            )

        _require_write(collection_id)

        # Index, collection tracking, file on disk, answer cache, audit —
        # one implementation shared with the admin "remove and block" action.
        try:
            outcome = governance.remove_document(collection_id, document_id)
        except KeyError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document {document_id} not found",
            )

        return {"message": f"Deleted document {document_id}", **outcome}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete document: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete document: {str(e)}",
        )


class ReportRequest(BaseModel):
    """Body for reporting a document."""
    reason: str = ""


@router.post(
    "/documents/{document_id}/report",
    summary="Report a document for administrator review",
    tags=["documents"],
)
def report_document(document_id: str, body: ReportRequest, request: Request, collection_id: str = "default"):
    """Anyone who can read a collection may flag one of its documents.

    The report lands in the audit trail and, when email is configured,
    in the admins' inboxes. The document itself is untouched — takedown is
    the admin's decision, in the Admin tab.
    """
    get_indexer(collection_id)  # read access check
    try:
        return governance.report_document(
            collection_id, document_id, body.reason, app_url=str(request.base_url)
        )
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document {document_id} not found")


class DocumentGovernanceUpdate(BaseModel):
    """Body for per-document governance changes."""
    sensitivity: str | None = None   # "" or null clears the override


@router.patch(
    "/documents/{document_id}/governance",
    summary="Set a per-document sensitivity label",
    tags=["documents"],
)
def update_document_governance(document_id: str, body: DocumentGovernanceUpdate, collection_id: str = "default"):
    """Override the collection's sensitivity label for one document.

    Owners and readwrite sharees may relabel; the audit trail records who.
    Policy status (quarantine/approve) is admin-only and lives under
    /api/admin — a user cannot clear their own document's hold.
    """
    get_indexer(collection_id)
    _require_write(collection_id)
    try:
        return governance.set_document_sensitivity(collection_id, document_id, body.sensitivity)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document {document_id} not found")
