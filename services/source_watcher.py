"""Background loop that keeps watched sources current, raises the alerts, and
says when a source has fallen behind its index.

A source is a folder (or a managed git clone) remembered by
``UploadService``. Turning *Watch* on stores ``watch: true`` and an interval on
its memory entry; this loop wakes every ``SOURCE_WATCH_TICK_SECONDS``, finds
the entries that are due and re-syncs them through the same ``sync_folder``
the Sync button uses, so there is exactly one way a source is brought up to
date. Anything that changed lands in the Updates feed and the optional webhook
(services/source_changes.py).

For a remote repository the cheap question comes first: ``git ls-remote`` asks
the server for the branch tip, and nothing is downloaded or hashed when it has
not moved. The tip is also asked of a remote repository nobody is watching, on
the default interval, and only remembered (``upstream_head``): that is how the
Sources panel can say "new commits upstream" next to a repository the person
chose to update by hand. A local checkout is always re-synced, because
uncommitted edits are changes too.

The cheap answer is only trusted when the index really holds the previous
sync: a clone that vanished with the data directory is made again, and a sync
whose indexing job failed (the server restarted, the embedder was down) is
repeated even though the tip has not moved. A source whose previous job is
still indexing is left alone until it finishes, so a slow import is never
queued twice.

``freshness`` is the read side of the same knowledge, for the Sources panel:
without touching the network it says whether a source is out of date and why
(HEAD moved, files edited, remote ahead, last run unfinished).

One bad source never stops the others: failures are written to the entry's
``last_error`` (shown in the Sources panel) and retried at the next interval.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import settings
from services import git_source
from services.app_database import app_db

logger = logging.getLogger(__name__)

_FIRST_RUN_DELAY_S = 45
_IN_FLIGHT = ("pending", "running")
_UNFINISHED = ("failed", "cancelled")

OUTCOMES = ("synced", "unchanged", "probed", "busy", "error")


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", ""))
    except ValueError:
        return None


def _job_status(entry: Dict[str, Any]) -> Optional[str]:
    """Status of the indexing job the source's last sync queued; None when
    that sync queued nothing (or the job row is gone)."""
    job_id = entry.get("job_id")
    if not job_id:
        return None
    try:
        job = app_db.get_upload_job(int(job_id))
    except Exception as e:  # noqa: BLE001 - freshness is advisory
        logger.debug(f"Could not read job {job_id}: {e}")
        return None
    return (job or {}).get("status")


def _monitored(entry: Dict[str, Any]) -> bool:
    """Watched sources are synced; unwatched remote repositories are probed."""
    return bool(entry.get("watch") or entry.get("git_url"))


def _due(entry: Dict[str, Any], now: datetime) -> bool:
    if not _monitored(entry):
        return False
    minutes = int(entry.get("interval_minutes") or settings.source_watch_default_minutes)
    last = _parse(entry.get("last_checked_at")) or _parse(entry.get("last_synced_at"))
    return last is None or now - last >= timedelta(minutes=max(1, minutes))


def monitored_sources() -> List[Dict[str, Any]]:
    """Every remembered source the loop looks after, across all collections:
    the watched ones, plus remote repositories (probed, not synced)."""
    from services.upload_service import upload_service

    out = []
    for collection in app_db.get_all_collections():
        cid = collection.get("id")
        if not cid:
            continue
        for entry in upload_service.list_sync_folders(cid):
            if _monitored(entry):
                out.append(dict(entry, collection_id=cid))
    return out


def watched_sources() -> List[Dict[str, Any]]:
    """Every remembered, watched source across all collections."""
    return [e for e in monitored_sources() if e.get("watch")]


def check_source(entry: Dict[str, Any]) -> str:
    """Look after one monitored source. Returns one of :data:`OUTCOMES`:

    * ``synced`` - re-synced (something may have been queued);
    * ``unchanged`` - a watched remote whose tip has not moved;
    * ``probed`` - an unwatched remote: its tip was recorded, nothing synced;
    * ``busy`` - the previous sync is still indexing, left alone;
    * ``error`` - recorded on the entry, retried next interval.
    """
    from services.upload_service import upload_service

    cid, path = entry["collection_id"], entry["path"]
    now = datetime.utcnow().isoformat()
    try:
        job_status = _job_status(entry)
        if job_status in _IN_FLIGHT:
            # Syncing again now would hash files the running job has not
            # stored yet and queue them a second time.
            return "busy"
        git_url = entry.get("git_url")
        if git_url:
            branch = entry.get("git_ref") or ""
            tip = git_source.remote_head(git_url, branch)
            if not entry.get("watch"):
                upload_service.update_sync_folder(
                    cid, path, last_checked_at=now, last_error=None, upstream_head=tip,
                )
                return "probed"
            index_is_current = (
                tip and tip == entry.get("git_head")
                and Path(path).is_dir()              # the clone is still there
                and job_status not in _UNFINISHED    # and its files really got indexed
            )
            if index_is_current:
                upload_service.update_sync_folder(
                    cid, path, last_checked_at=now, last_error=None, upstream_head=tip,
                )
                return "unchanged"
            git_source.ensure_clone(git_url, branch)
        upload_service.sync_folder(
            path=path, collection_id=cid,
            recursive=entry.get("recursive", True) is not False,
            file_extensions=entry.get("file_extensions"),
            exclude_patterns=entry.get("exclude_patterns"),
            prune_missing=bool(entry.get("prune_missing")) or bool(git_url),
            uploaded_by=entry.get("watch_owner"),
            trigger="watch",
        )
        return "synced"
    except Exception as e:  # noqa: BLE001 - one source must never stop the loop
        logger.warning(f"Watch of {path} in '{cid}' failed: {e}")
        try:
            upload_service.update_sync_folder(cid, path, last_checked_at=now, last_error=str(e)[:300])
        except Exception:  # noqa: BLE001
            pass
        return "error"


def run_due(now: Optional[datetime] = None) -> Dict[str, int]:
    """One pass over the monitored sources that are due. Returns outcome counts."""
    now = now or datetime.utcnow()
    counts = {outcome: 0 for outcome in OUTCOMES}
    for entry in monitored_sources():
        if _due(entry, now):
            counts[check_source(entry)] += 1
    return counts


def freshness(entry: Dict[str, Any]) -> Dict[str, Any]:
    """How a remembered source relates to its index right now.

    Returns ``out_of_date`` with a one-line ``stale_reason``, ``indexing``
    (the last sync's job is still running), ``current_head`` (a local
    checkout's HEAD now) and ``new_commits`` (how far that HEAD moved).

    Never touches the network: a remote repository is judged by the tip the
    watcher last recorded. A local checkout costs one or two git calls and
    no hashing (``git_source.worktree_fingerprint``). A plain folder has no
    cheap answer and is reported as current; Sync tells.
    """
    out: Dict[str, Any] = {
        "out_of_date": False, "stale_reason": None, "indexing": False,
        "current_head": None, "new_commits": None,
    }
    job_status = _job_status(entry)
    if job_status in _IN_FLIGHT:
        out["indexing"] = True
        return out
    if job_status in _UNFINISHED:
        out.update(out_of_date=True, stale_reason="The last indexing run did not finish")
        return out

    if entry.get("git_url"):
        upstream, head = entry.get("upstream_head"), entry.get("git_head")
        if upstream and head and upstream != head:
            out.update(out_of_date=True, stale_reason="New commits upstream")
        elif not Path(entry.get("path") or "").is_dir():
            out.update(out_of_date=True, stale_reason="The clone is gone; Sync makes it again")
        return out

    if not entry.get("git"):
        return out
    path = Path(entry["path"])
    if not path.is_dir():
        return out
    info = git_source.head_info(path)
    if info is None:
        return out
    out["current_head"] = info["head"]
    remembered = entry.get("git_head")
    if remembered and info["head"] != remembered:
        n = git_source.count_commits(path, remembered, info["head"])
        out["new_commits"] = n
        if n:
            reason = f"{n} new commit{'s' if n != 1 else ''} since the last sync"
        else:
            reason = "HEAD moved since the last sync"
        out.update(out_of_date=True, stale_reason=reason)
        return out
    fingerprint = entry.get("git_fingerprint")
    if fingerprint and git_source.worktree_fingerprint(path, head=info["head"]) != fingerprint:
        out.update(out_of_date=True, stale_reason="Files changed since the last sync")
    return out


async def watcher_loop() -> None:
    tick = int(settings.source_watch_tick_seconds)
    if tick <= 0:
        logger.info("Source watching disabled (SOURCE_WATCH_TICK_SECONDS=0)")
        return
    await asyncio.sleep(_FIRST_RUN_DELAY_S)
    while True:
        try:
            counts = await asyncio.to_thread(run_due)
            if counts["synced"] or counts["error"]:
                logger.info(f"Source watch pass: {counts}")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Source watch pass failed: {e}")
        await asyncio.sleep(tick)
