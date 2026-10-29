"""Background research jobs: analyze, assess or brief many papers unattended.

"Prepare reviews while I sleep": a job is submitted with the papers, the
question and the model, and works through them one at a time on a worker
thread while the researcher does something else. Progress, per-paper
failures and the results land in the collection's ``research_jobs`` /
``research_briefs`` / ``research_assessments`` tables, so they are waiting
when the researcher comes back (the Research tab's review queue).

The model provider is built from the submitting request (its key, its
model) and held in memory for the job's lifetime only; it is never written
down. Like index jobs, research jobs do not survive a restart: a job the
process no longer knows is reported failed with the reason, and finished
papers keep their results. ``not_before`` lets a job wait for a quiet hour.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import config
from services.research.store import ResearchStore, now_iso

logger = logging.getLogger(__name__)

KINDS = ("analyze", "assess", "brief")
MAX_DELAY_HOURS = 48
RESTART_REASON = "Stopped: the server restarted while this job was running."


@dataclass
class _LiveJob:
    job_id: str
    collection_id: str
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: Optional[threading.Thread] = None


_live: Dict[str, _LiveJob] = {}
_live_lock = threading.Lock()
_slots: Optional[threading.BoundedSemaphore] = None
_slots_lock = threading.Lock()


def _job_slots() -> threading.BoundedSemaphore:
    global _slots
    with _slots_lock:
        if _slots is None:
            limit = max(1, int(getattr(config.settings, "research_max_concurrent_jobs", 1) or 1))
            _slots = threading.BoundedSemaphore(limit)
        return _slots


def is_live(job_id: str) -> bool:
    with _live_lock:
        return job_id in _live


def reconcile(store: ResearchStore) -> None:
    """Mark jobs this process is not running as failed (they died with an
    earlier process)."""
    for job in store.list_jobs(limit=200):
        if job["status"] in ("queued", "running") and not is_live(job["job_id"]):
            store.update_job(job["job_id"], status="failed", finished_at=now_iso(), current=RESTART_REASON)


def list_jobs(store: ResearchStore, limit: int = 50) -> List[dict]:
    reconcile(store)
    return store.list_jobs(limit=limit)


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def start_job(
    *,
    collection_id: str,
    store: ResearchStore,
    kind: str,
    documents: List[dict],
    work: Callable[[dict, str], Any],
    params: Dict[str, Any],
    created_by: Optional[str] = None,
    finish: Optional[Callable[[], None]] = None,
    not_before: Optional[str] = None,
    budget_check: Optional[Callable[[], Optional[str]]] = None,
) -> dict:
    """Queue ``work(document, job_id)`` for each document on a background thread.

    ``work`` raises to record a per-paper failure; the job carries on with
    the next paper. ``finish`` runs once after the last paper (e.g. library
    matching after an analyze batch). ``budget_check`` returns a message to
    stop the job early (the user's daily token budget ran out)."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    limit = int(getattr(config.settings, "research_max_job_documents", 200) or 200)
    if len(documents) > limit:
        raise ValueError(f"A job may cover at most {limit} papers; select fewer.")
    try:
        start_at = _parse_time(not_before)
    except ValueError as exc:
        raise ValueError(f"not_before is not a date and time: {not_before!r}") from exc
    if start_at and (start_at - datetime.now(timezone.utc)).total_seconds() > MAX_DELAY_HOURS * 3600:
        raise ValueError(f"A job can be scheduled at most {MAX_DELAY_HOURS} hours ahead.")
    job_id = uuid.uuid4().hex[:12]
    job_params = {**params, "document_ids": [d["document_id"] for d in documents]}
    if start_at:
        job_params["not_before"] = start_at.isoformat(timespec="minutes")
    live = _LiveJob(job_id=job_id, collection_id=collection_id)
    # Registered before the row exists, so a concurrent listing never
    # mistakes the new job for one an earlier process left behind.
    with _live_lock:
        _live[job_id] = live
    try:
        job = store.create_job(job_id, kind, job_params, total=len(documents), created_by=created_by)
    except Exception:
        with _live_lock:
            _live.pop(job_id, None)
        raise

    def run() -> None:
        errors: List[dict] = []
        done = failed = 0
        try:
            if start_at and start_at > datetime.now(timezone.utc):
                store.update_job(job_id, current=f"Waiting until {start_at.isoformat(timespec='minutes')}")
                while (remaining := (start_at - datetime.now(timezone.utc)).total_seconds()) > 0:
                    if live.cancel.wait(timeout=min(remaining, 60)):
                        store.update_job(job_id, status="cancelled", finished_at=now_iso(), current=None)
                        return
            store.update_job(job_id, current="Waiting for another research job to finish")
            slots = _job_slots()
            while not slots.acquire(timeout=5):
                if live.cancel.is_set():
                    store.update_job(job_id, status="cancelled", finished_at=now_iso(), current=None)
                    return
            try:
                store.update_job(job_id, status="running", started_at=now_iso(), current=None)
                for doc in documents:
                    if live.cancel.is_set():
                        store.update_job(job_id, status="cancelled", finished_at=now_iso(), current=None,
                                         done=done, failed=failed, errors=errors)
                        return
                    if budget_check:
                        stop = budget_check()
                        if stop:
                            errors.append({"document_id": None, "filename": None, "error": stop})
                            store.update_job(job_id, status="failed", finished_at=now_iso(), current=stop,
                                             done=done, failed=failed, errors=errors)
                            return
                    store.update_job(job_id, current=doc.get("filename") or doc["document_id"])
                    try:
                        work(doc, job_id)
                        done += 1
                    except Exception as exc:  # noqa: BLE001 - one paper never sinks the batch
                        failed += 1
                        logger.warning("Research %s job %s: %s failed: %s", kind, job_id,
                                       doc.get("filename"), exc)
                        errors.append({"document_id": doc["document_id"], "filename": doc.get("filename"),
                                       "error": str(exc)[:300] or exc.__class__.__name__})
                    store.update_job(job_id, done=done, failed=failed, errors=errors)
                if finish:
                    try:
                        finish()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Research job %s finishing step failed: %s", job_id, exc)
                status = "done" if done or not failed else "failed"
                store.update_job(job_id, status=status, finished_at=now_iso(), current=None,
                                 done=done, failed=failed, errors=errors)
            finally:
                slots.release()
        except Exception as exc:  # noqa: BLE001 - the job row must not stay "running"
            logger.exception("Research job %s crashed", job_id)
            store.update_job(job_id, status="failed", finished_at=now_iso(), current=str(exc)[:300])
        finally:
            with _live_lock:
                _live.pop(job_id, None)

    thread = threading.Thread(target=run, name=f"research-{kind}-{job_id}", daemon=True)
    live.thread = thread
    thread.start()
    return job


def cancel_job(store: ResearchStore, job_id: str) -> bool:
    with _live_lock:
        live = _live.get(job_id)
    if live is None:
        job = store.get_job(job_id)
        if job and job["status"] in ("queued", "running"):
            store.update_job(job_id, status="failed", finished_at=now_iso(), current=RESTART_REASON)
        return False
    live.cancel.set()
    return True


def wait_for(job_id: str, timeout: float = 30.0) -> None:
    """Block until a job's thread ends (tests, and the shutdown hook)."""
    with _live_lock:
        live = _live.get(job_id)
    if live and live.thread:
        live.thread.join(timeout)
