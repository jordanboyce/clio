"""Per-collection storage cap.

Every collection may hold at most ``COLLECTION_STORAGE_LIMIT_BYTES`` of
source material (5 GiB by default), measured as the sum of ``file_size``
over its ``documents`` rows — the bytes of every upload, staged copy,
in-place reference and agent-written source the collection has indexed.

Why bytes of source and not bytes of index: the number a person can reason
about is "how much have I put in", and it is the same figure whether the
collection lives on SQLite or Postgres, whether OCR ran, or which embedding
model was chosen. Index size is an operator concern reported elsewhere.

Enforcement is at the ingest entry points (``api/documents.py``, the bulk
job in ``services/upload_service.py`` and ``write_document`` over MCP),
not inside the indexer: by the time the indexer sees a file its bytes are
already on disk, and refusing early means a rejected upload never waits
in the embedding queue. Bulk jobs prepare files on several threads and
commit them in batches, so a plain "usage + this file" check would let a
whole batch slip past the cap at once; the in-process reservation ledger
below counts bytes that are accepted but not yet committed. One process,
one ledger (see docs/DEPLOYMENT.md on the single-process model).
"""

from __future__ import annotations

import logging
import shutil
import threading
from contextlib import contextmanager
from typing import Dict, Iterator, Optional

import config

logger = logging.getLogger(__name__)

_reserved: Dict[str, int] = {}
_lock = threading.Lock()


class StorageLimitExceeded(Exception):
    """Raised when accepting ``incoming_bytes`` would push a collection past its cap."""

    status_code = 413

    def __init__(self, collection_id: str, incoming_bytes: int, usage_bytes: int, limit_bytes: int,
                 filename: Optional[str] = None):
        self.collection_id = collection_id
        self.incoming_bytes = incoming_bytes
        self.usage_bytes = usage_bytes
        self.limit_bytes = limit_bytes
        self.filename = filename
        remaining = max(0, limit_bytes - usage_bytes)
        what = f"{filename} ({format_bytes(incoming_bytes)})" if filename else format_bytes(incoming_bytes)
        super().__init__(
            f"Adding {what} would exceed this collection's storage limit of "
            f"{format_bytes(limit_bytes)} ({format_bytes(usage_bytes)} used, "
            f"{format_bytes(remaining)} free). Remove sources or start a new collection."
        )

    def to_detail(self) -> dict:
        return {
            "detail": str(self),
            "error": "storage_limit_exceeded",
            "collection_id": self.collection_id,
            "storage_bytes": self.usage_bytes,
            "storage_limit_bytes": self.limit_bytes,
            "incoming_bytes": self.incoming_bytes,
            "filename": self.filename,
        }


class DiskSpaceLow(StorageLimitExceeded):
    """The data directory would drop below MIN_FREE_DISK_MB. Every catch site
    of StorageLimitExceeded refuses it the same way, with 507."""

    status_code = 507

    def __init__(self, collection_id: str, incoming_bytes: int, free: int, keep: int,
                 filename: Optional[str] = None):
        self.collection_id = collection_id
        self.incoming_bytes = incoming_bytes
        self.usage_bytes = 0
        self.limit_bytes = 0
        self.filename = filename
        self.free_bytes = free
        self.keep_bytes = keep
        what = filename or "this upload"
        Exception.__init__(
            self,
            f"The server is almost out of disk space, so {what} was not added. Nothing was "
            "lost. Ask an administrator to free space, then try again.",
        )

    def to_detail(self) -> dict:
        return {
            "detail": str(self),
            "error": "disk_space_low",
            "collection_id": self.collection_id,
            "incoming_bytes": self.incoming_bytes,
            "filename": self.filename,
        }


def min_free_bytes() -> int:
    return max(0, int(getattr(config.settings, "min_free_disk_mb", 0) or 0)) * 1024 * 1024


def free_disk_bytes() -> Optional[int]:
    """Free space where the data directory lives, or None when it cannot be read."""
    try:
        return int(shutil.disk_usage(config.settings.data_dir).free)
    except OSError:
        return None


def check_disk(collection_id: str, incoming_bytes: int, filename: Optional[str] = None) -> None:
    """Raise DiskSpaceLow when ``incoming_bytes`` would leave less than MIN_FREE_DISK_MB."""
    keep = min_free_bytes()
    if not keep:
        return
    free = free_disk_bytes()
    incoming = max(0, int(incoming_bytes or 0))
    if free is not None and free - incoming < keep:
        logger.warning(f"Refused {filename or 'an ingest'}: {format_bytes(free)} free, "
                       f"{format_bytes(keep)} kept (MIN_FREE_DISK_MB)")
        raise DiskSpaceLow(collection_id, incoming, free, keep, filename)


def format_bytes(n: int) -> str:
    """Human-readable size: 5 GB, 1.2 GB, 850 MB, 12 KB, 300 B."""
    n = int(n or 0)
    for unit, size in (("TB", 1024 ** 4), ("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= size:
            value = n / size
            return f"{value:.0f} {unit}" if value >= 10 or value == int(value) else f"{value:.1f} {unit}"
    return f"{n} B"


def limit_bytes() -> int:
    """The configured cap; 0 means unlimited."""
    return max(0, int(config.settings.collection_storage_limit_bytes or 0))


def usage_bytes(collection_id: str) -> int:
    """Committed bytes: SUM(file_size) over the collection's documents."""
    from services.indexer_manager import indexer_manager

    try:
        indexer = indexer_manager.get_indexer(collection_id)
        return int(indexer.vector_store.metadata_store.get_storage_bytes())
    except Exception as e:  # a collection with no index yet holds nothing
        logger.debug(f"storage usage unavailable for {collection_id}: {e}")
        return 0


def reserved_bytes(collection_id: str) -> int:
    with _lock:
        return _reserved.get(collection_id, 0)


def describe(collection_id: str) -> dict:
    """The numbers the UI shows: used, limit, remaining, percent."""
    used = usage_bytes(collection_id)
    limit = limit_bytes()
    remaining = max(0, limit - used) if limit else None
    percent = round(min(100.0, used / limit * 100), 1) if limit else 0.0
    return {
        "storage_bytes": used,
        "storage_limit_bytes": limit,
        "storage_remaining_bytes": remaining,
        "storage_percent": percent,
        "storage_unlimited": limit == 0,
    }


def check(collection_id: str, incoming_bytes: int, filename: Optional[str] = None) -> None:
    """Raise StorageLimitExceeded if ``incoming_bytes`` does not fit. Cheap; no reservation."""
    check_disk(collection_id, incoming_bytes, filename)
    limit = limit_bytes()
    if not limit:
        return
    incoming = max(0, int(incoming_bytes or 0))
    used = usage_bytes(collection_id) + reserved_bytes(collection_id)
    if used + incoming > limit:
        raise StorageLimitExceeded(collection_id, incoming, used, limit, filename)


@contextmanager
def reserve(collection_id: str, incoming_bytes: int, filename: Optional[str] = None) -> Iterator[None]:
    """Check, then hold ``incoming_bytes`` against the cap for the duration of the block.

    Use around save+index of one file so concurrent ingests into the same
    collection see each other's in-flight bytes. Released on exit whether
    the file was committed (it now counts through SUM(file_size)) or
    failed (it counts for nothing).
    """
    check_disk(collection_id, incoming_bytes, filename)
    limit = limit_bytes()
    if not limit:
        yield
        return
    incoming = max(0, int(incoming_bytes or 0))
    with _lock:
        used = usage_bytes(collection_id) + _reserved.get(collection_id, 0)
        if used + incoming > limit:
            raise StorageLimitExceeded(collection_id, incoming, used, limit, filename)
        _reserved[collection_id] = _reserved.get(collection_id, 0) + incoming
    try:
        yield
    finally:
        with _lock:
            left = _reserved.get(collection_id, 0) - incoming
            if left > 0:
                _reserved[collection_id] = left
            else:
                _reserved.pop(collection_id, None)


def take(collection_id: str, incoming_bytes: int, filename: Optional[str] = None) -> int:
    """Non-context form of reserve() for pipelines that release later (bulk jobs).

    Returns the number of bytes held; pass it back to release().
    """
    check_disk(collection_id, incoming_bytes, filename)
    limit = limit_bytes()
    if not limit:
        return 0
    incoming = max(0, int(incoming_bytes or 0))
    with _lock:
        used = usage_bytes(collection_id) + _reserved.get(collection_id, 0)
        if used + incoming > limit:
            raise StorageLimitExceeded(collection_id, incoming, used, limit, filename)
        _reserved[collection_id] = _reserved.get(collection_id, 0) + incoming
    return incoming


def release(collection_id: str, held_bytes: int) -> None:
    if not held_bytes:
        return
    with _lock:
        left = _reserved.get(collection_id, 0) - held_bytes
        if left > 0:
            _reserved[collection_id] = left
        else:
            _reserved.pop(collection_id, None)


def _reset_for_tests() -> None:
    with _lock:
        _reserved.clear()
