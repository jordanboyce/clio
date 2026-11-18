"""Analyze papers as they are indexed (roadmap R5, work order A4).

Analysis needs no model, so there is no reason for a fresh PDF to read
"Not analyzed" until someone presses a button. After an index job adds
papers, they are queued as an ordinary Library ``analyze`` job: it waits for
a free Library job slot (low priority next to the index job, which has
already finished), shows in the Library's job strip with Stop, and can never
fail the index job, because it starts only after that job is done and every
error here is logged, not raised.

With the catalogues blocked, each host is given one chance and then a rest
(``lookup`` R6), so the job finishes from the sources' own text without
waiting out a timeout per source.
"""

from __future__ import annotations

import logging
from typing import Iterable, List, Optional

import config

logger = logging.getLogger(__name__)

AUTOMATIC = "automatic"


def enabled() -> bool:
    return bool(getattr(config.settings, "library_auto_analyze", True))


def analyze_after_index(collection_id: str, document_ids: Iterable[str],
                        created_by: Optional[str] = None) -> List[dict]:
    """Queue analysis of the papers among ``document_ids`` that have none
    yet. Returns the jobs started (none when there is nothing to do)."""
    ids = [d for d in dict.fromkeys(document_ids or []) if d]
    if not ids or not enabled():
        return []
    try:
        from services import governance
        from services.indexer_manager import indexer_manager
        from services.research import jobs, library

        indexer = indexer_manager.get_indexer(collection_id)
        store = library.research_store(indexer)
        metadata = indexer.vector_store.metadata_store
        docs = [metadata.get_document_info(d) for d in ids]
        docs = [d for d in docs if d and library.is_paper(d) and not governance.is_hidden(d)]
        if not docs:
            return []
        known = store.get_papers([d["document_id"] for d in docs])
        docs = [d for d in docs if not (known.get(d["document_id"]) or {}).get("analyzed_at")]
        if not docs:
            return []

        def papers():
            return [d for d in indexer.list_documents() if library.is_paper(d) and not governance.is_hidden(d)]

        size = jobs.max_documents()
        started = []
        for start in range(0, len(docs), size):
            started.append(jobs.start_job(
                collection_id=collection_id, store=store, kind="analyze", documents=docs[start:start + size],
                work=lambda d, _job_id: library.analyze_document(collection_id, indexer, d["document_id"],
                                                                 use_registry=True, store=store),
                finish=lambda: library.match_library(store, papers()),
                params={"use_registry": True, "automatic": True},
                created_by=created_by or AUTOMATIC,
            ))
        logger.info("Queued Library analysis of %d new source(s) in %s", len(docs), collection_id)
        return started
    except Exception as exc:  # noqa: BLE001 - never a reason for indexing to fail
        logger.warning("Could not queue Library analysis for %s: %s", collection_id, exc)
        return []
