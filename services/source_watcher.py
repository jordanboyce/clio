"""Background loop that keeps watched sources current and raises the alerts.

A source is a folder (or a managed git clone) remembered by
``UploadService``. Turning *Watch* on stores ``watch: true`` and an interval on
its memory entry; this loop wakes every ``SOURCE_WATCH_TICK_SECONDS``, finds
the entries that are due and re-syncs them through the same ``sync_folder``
the Sync button uses, so there is exactly one way a source is brought up to
date. Anything that changed lands in the Updates feed and the optional webhook
(services/source_changes.py).

For a remote repository the cheap question comes first: ``git ls-remote`` asks
the server for the branch tip, and nothing is downloaded or hashed when it has
not moved. A local checkout is always re-synced, because uncommitted edits are
changes too.

One bad source never stops the others: failures are written to the entry's
``last_error`` (shown in the Sources panel) and retried at the next interval.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from config import settings
from services import git_source
from services.app_database import app_db

logger = logging.getLogger(__name__)

_FIRST_RUN_DELAY_S = 45


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", ""))
    except ValueError:
        return None


def _due(entry: Dict[str, Any], now: datetime) -> bool:
    if not entry.get("watch"):
        return False
    minutes = int(entry.get("interval_minutes") or settings.source_watch_default_minutes)
    last = _parse(entry.get("last_checked_at")) or _parse(entry.get("last_synced_at"))
    return last is None or now - last >= timedelta(minutes=max(1, minutes))


def watched_sources() -> List[Dict[str, Any]]:
    """Every remembered, watched source across all collections."""
    from services.upload_service import upload_service

    out = []
    for collection in app_db.get_all_collections():
        cid = collection.get("id")
        if not cid:
            continue
        for entry in upload_service.list_sync_folders(cid):
            if entry.get("watch"):
                out.append(dict(entry, collection_id=cid))
    return out


def check_source(entry: Dict[str, Any]) -> str:
    """Bring one watched source up to date. Returns "synced", "unchanged" or "error"."""
    from services.upload_service import upload_service

    cid, path = entry["collection_id"], entry["path"]
    now = datetime.utcnow().isoformat()
    try:
        git_url = entry.get("git_url")
        if git_url:
            branch = entry.get("git_ref") or ""
            tip = git_source.remote_head(git_url, branch)
            if tip and tip == entry.get("git_head"):
                upload_service.update_sync_folder(cid, path, last_checked_at=now, last_error=None)
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
    """One pass over the watched sources that are due. Returns outcome counts."""
    now = now or datetime.utcnow()
    counts = {"synced": 0, "unchanged": 0, "error": 0}
    for entry in watched_sources():
        if _due(entry, now):
            counts[check_source(entry)] += 1
    return counts


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
