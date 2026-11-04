"""Background generation of saved artifacts.

Generating takes minutes and must not depend on the page that asked for it:
the request records a queued row and returns, a worker thread does the work,
and the row is what any later visit reads. The model key stays in the job's
closure for its lifetime and is never written down.

The worker runs in a copy of the request's context, because the chat loop reads
the caller's identity from a contextvar for access checks and usage records.

Like research jobs, these do not survive a restart. A row left active by an
earlier process is reported failed by ``effective_state``; reading it writes
nothing. Deleting an artifact while its job runs is allowed: the model call
finishes and its result is dropped.
"""

from __future__ import annotations

import contextvars
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Mapping

from services.artifact_store import ACTIVE_STATUSES, ArtifactStore

logger = logging.getLogger(__name__)

MAX_CONCURRENT_JOBS = 2
MAX_ACTIVE_JOBS = 8
RESTART_REASON = "Stopped: the server restarted while this was generating."
EMPTY_REASON = "The model returned nothing to save. Try again, or try another model."

_threads: Dict[str, threading.Thread] = {}
_lock = threading.Lock()
_slots = threading.BoundedSemaphore(MAX_CONCURRENT_JOBS)


class JobQueueFull(RuntimeError):
    """Too many artifacts are generating already."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_live(artifact_id: str) -> bool:
    with _lock:
        return artifact_id in _threads


def effective_state(artifact: Mapping[str, Any]) -> dict:
    """The status to report: an active row nothing is working on has failed."""
    if artifact.get("status") in ACTIVE_STATUSES and not is_live(artifact["id"]):
        return {"status": "failed", "error": RESTART_REASON}
    return {"status": artifact.get("status"), "error": artifact.get("error")}


def start(store: ArtifactStore, work: Callable[[], Dict[str, Any]], **fields: Any) -> dict:
    """Save a queued artifact and generate it on a worker thread.

    ``work`` returns the fields to save (``content`` and the rest) or raises
    with a message fit to show. Raises JobQueueFull when the queue is full.
    """
    artifact_id = uuid.uuid4().hex[:12]
    context = contextvars.copy_context()

    def run() -> None:
        try:
            with _slots:
                # Deleted while it waited: nothing has been spent, so stop.
                if not store.update(artifact_id, status="running", started_at=_now()):
                    return
                result = work()
            if not (result.get("content") or "").strip():
                raise RuntimeError(EMPTY_REASON)
            store.update(artifact_id, status="done", finished_at=_now(), error=None, **result)
        except Exception as exc:  # noqa: BLE001 - the row must not stay active
            logger.warning("Artifact %s failed: %s", artifact_id, exc)
            store.update(artifact_id, status="failed", finished_at=_now(),
                         error=str(exc)[:300] or exc.__class__.__name__)
        finally:
            with _lock:
                _threads.pop(artifact_id, None)

    thread = threading.Thread(target=context.run, args=(run,), name=f"artifact-{artifact_id}", daemon=True)
    # Registered before the row exists, so a concurrent listing never mistakes
    # the new job for one an earlier process left behind.
    with _lock:
        if len(_threads) >= MAX_ACTIVE_JOBS:
            raise JobQueueFull("Too many reports are generating. Try again when one finishes.")
        _threads[artifact_id] = thread
    try:
        artifact = store.create(artifact_id, status="queued", created_at=_now(), **fields)
    except Exception:
        with _lock:
            _threads.pop(artifact_id, None)
        raise
    thread.start()
    return artifact


def wait_for(artifact_id: str, timeout: float = 30.0) -> None:
    """Block until a job's thread ends (tests)."""
    with _lock:
        thread = _threads.get(artifact_id)
    if thread:
        thread.join(timeout)
