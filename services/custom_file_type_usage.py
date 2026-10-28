"""How many indexed files use a custom file type, collection by collection.

Re-indexing is per collection (and gated on being that collection's owner), so
the admin needs the count per collection, not one total. The counts come from
each collection's own MetadataStore through the indexer the caller already
reaches via the API choke point (``api.deps.get_indexer``), so a collection the
caller cannot see is not counted and not named, and quarantined documents are
left out exactly as they are from every other listing.

"Out of date" means indexed before the profile's last change to how files are
read (``reading_updated_at``). Documents carry their index time already, so no
schema change is needed to say "3 files were indexed with an older profile".
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from services import custom_file_types as cft

logger = logging.getLogger(__name__)

_PAGE = 1000


def _parse_utc(value: Any) -> Optional[datetime]:
    """A stored timestamp as an aware UTC datetime. Index times are naive UTC."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def reading_stamp(profile: Optional[Dict[str, Any]]) -> Optional[datetime]:
    """When the profile last changed how files are read (None without a profile)."""
    if not profile:
        return None
    return _parse_utc(profile.get("reading_updated_at") or profile.get("updated_at"))


def count_in_indexer(indexer: Any, extension: str, since: Optional[datetime] = None) -> Dict[str, int]:
    """``{documents, out_of_date}`` for one collection's visible documents.

    The match is the document grouping key (last suffix, case-insensitive)
    compared exactly, never a LIKE pattern, so ``.acme`` does not count
    ``.acmeish`` and no wildcard in a name can widen the match.
    """
    ext = cft.normalize_extension(extension)
    rows, total = indexer.find_documents(limit=0, keys=[ext])
    out_of_date = 0
    if since is not None and total:
        offset = 0
        while offset < total:
            page, _ = indexer.find_documents(limit=_PAGE, offset=offset, keys=[ext], sort="name")
            if not page:
                break
            for row in page:
                indexed = _parse_utc(row.get("upload_timestamp"))
                if indexed is not None and indexed < since:
                    out_of_date += 1
            offset += len(page)
    return {"documents": int(total), "out_of_date": out_of_date}


def usage_by_collection(
    extension: str,
    collections: Iterable[Dict[str, Any]],
    get_indexer: Callable[[str], Any],
) -> Dict[str, Any]:
    """Per-collection usage of `extension` across the collections given.

    `collections` is what the caller may see (``collection_service.
    get_all_collections``); `get_indexer` is the access-checked accessor. A
    collection that cannot be opened is reported with an ``error`` rather than
    failing the whole answer. Only collections that hold at least one matching
    file are listed, so the panel never offers a no-op re-index.
    """
    ext = cft.normalize_extension(extension)
    profile = cft.registry.get(ext)
    since = reading_stamp(profile)

    rows: List[Dict[str, Any]] = []
    unavailable: List[Dict[str, Any]] = []
    for c in collections:
        cid = c.get("id")
        if not cid:
            continue
        try:
            counts = count_in_indexer(get_indexer(cid), ext, since)
        except Exception as e:  # one unreadable collection must not hide the rest
            logger.warning(f"Could not count {ext} files in collection '{cid}': {e}")
            unavailable.append({"collection_id": cid, "name": c.get("name") or cid,
                                "error": str(getattr(e, "detail", None) or e)})
            continue
        if not counts["documents"]:
            continue
        rows.append({
            "collection_id": cid,
            "name": c.get("name") or cid,
            # May this caller re-index it? Re-indexing changes how the
            # collection is built, which is the owner's call.
            "can_reindex": (c.get("permission") or "owner") == "owner",
            **counts,
        })

    return {
        "extension": ext,
        "registered": profile is not None,
        "enabled": bool(profile and profile.get("enabled")),
        "reading_updated_at": (profile or {}).get("reading_updated_at") or (profile or {}).get("updated_at") or "",
        "collections": rows,
        "unavailable": unavailable,
        "total_documents": sum(r["documents"] for r in rows),
        "total_out_of_date": sum(r["out_of_date"] for r in rows),
    }
