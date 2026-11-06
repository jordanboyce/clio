"""One writer at a time on the named documents of a collection.

A write reads what a name holds, decides, indexes and swaps; two of them
interleaved lose updates, leave several rows for one name, or delete a file
another writer just put in place. Clio is one process by design
(docs/DEPLOYMENT.md), so an in-process lock covers every writer.

Searches and reads do not take it. During an agent's replace they can briefly
see both the old version and the new one, and never neither.

Bulk index jobs do not hold it while they embed, which can take many minutes.
They avoid other writers by claiming an unused file name instead
(upload_service._claim_stored_path), and take it only to settle which version of
a file stays (governance.retire_older_versions).
"""

from __future__ import annotations

import functools
import threading
from contextlib import contextmanager

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


class CollectionBusy(ValueError):
    """Another writer kept the collection for longer than this caller will wait."""


@contextmanager
def collection_write_lock(collection_id: str, timeout: float = -1):
    """Hold the collection against every other writer; by default, wait as long as it takes.

    Re-entrant, so a writer can call a shared step that also takes it.
    """
    with _locks_guard:
        lock = _locks.setdefault(collection_id, threading.RLock())
    if not lock.acquire(timeout=timeout):
        raise CollectionBusy(
            f"Another write to collection '{collection_id}' is still running after "
            f"{timeout:.0f} seconds. Nothing was changed; retry shortly."
        )
    try:
        yield
    finally:
        lock.release()


def holding_collection_lock(fn):
    """Run ``fn(collection_id, ...)`` under that collection's write lock."""
    @functools.wraps(fn)
    def locked(collection_id, *args, **kwargs):
        with collection_write_lock(collection_id):
            return fn(collection_id, *args, **kwargs)
    return locked
