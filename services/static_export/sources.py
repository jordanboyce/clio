"""What the manifest says about each source: its entry, its readable title."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from services.static_export.common import _title

logger = logging.getLogger(__name__)


def _library_titles(collection_id: str, document_ids: List[str]) -> Dict[str, str]:
    """Titles the Library holds for these sources, where it is sure of them:
    one marked "needs checking" is left out, and the file name is shown."""
    from services.collection_service import collection_service
    from services.research import library
    from services.research.store import ResearchStore

    try:
        # The store itself, not an indexer: the export never opens one.
        store = ResearchStore(collection_service.get_indexes_path(collection_id) / "metadata.db")
        papers = store.get_papers(document_ids)
    except Exception as e:  # noqa: BLE001 - a nicer title, never a reason to fail
        logger.info(f"Static export: no Library titles for {collection_id}: {e}")
        return {}
    titles = {}
    for doc_id, paper in (papers or {}).items():
        title = str((paper or {}).get("title") or "").strip()
        if title and "title" not in library.needs_checking(paper):
            titles[doc_id] = title
    return titles


def _document_entry(doc: Dict[str, Any], passages: int, link: Optional[Dict[str, Any]],
                    title: Optional[str] = None) -> Dict[str, Any]:
    entry = {"id": doc["document_id"], "name": doc["filename"], "title": title or _title(doc["filename"]),
             "pages": doc.get("num_pages"), "passages": passages}
    if link:
        entry["link"] = link
    return entry
