"""Search several collections and merge the results into one ranking.

Asking across all collections (Ask with the scope set to all, the MCP tool
search_all_collections, the benchmark's cross-collection questions) comes
through here, so the merge is one piece of code with one test.

Why the merge is not a sort on ``similarity_score`` (B1): that is the
first-stage score, which the reranker does not change, so sorting on it
threw the reranker's order away. It is also not comparable between
collections: its keyword half is divided by each collection's own best
keyword score, so every collection's best keyword hit scores full marks
however weak it is. The cross-encoder's score is comparable (it judges the
question and the passage alone), so results are merged on it when every
collection was reranked.

Without a reranker there is no comparable score, and merging by rank (each
collection's best first) was measured and was worse: on the retrieval_hard
benchmark cross-collection recall@10 fell from 0.15 to 0.10, because the
collection that holds the answer usually also holds several of the best
matches. So that case keeps the first-stage score.
"""

from __future__ import annotations

import contextvars
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

Hit = Tuple[Any, str]

# More than this many collection searches at once only queue for the scan
# gate (SCAN_MAX_CONCURRENT) inside each one.
_MAX_PARALLEL = 8


def _parallelism(n: int) -> int:
    try:
        from config import settings
        gate = int(getattr(settings, "scan_max_concurrent", 0) or 0)
    except Exception:  # noqa: BLE001
        gate = 0
    return max(1, min(n, _MAX_PARALLEL, gate or _MAX_PARALLEL))


def search_each(
    targets: Sequence[Tuple[str, Any]],
    query: str,
    top_k: int,
    *,
    on_error: Optional[Callable[[str, Exception], None]] = None,
    **search_kwargs: Any,
) -> Dict[str, dict]:
    """``{collection_id: search output}`` for every target whose search
    worked, searched in parallel. A failure is left out and ``on_error`` is
    told (or it is logged)."""
    def one(target):
        cid, indexer = target
        try:
            return cid, indexer.search(query=query, top_k=top_k, **search_kwargs), None
        except Exception as e:  # noqa: BLE001 - one bad collection must not fail the rest
            return cid, None, e

    if len(targets) <= 1:
        outcomes = [one(t) for t in targets]
    else:
        # Each search runs in the caller's context (who is asking, where the
        # answer is going): one copy per search, since a context cannot be
        # entered by two threads at once.
        with ThreadPoolExecutor(max_workers=_parallelism(len(targets)),
                                thread_name_prefix="clio-collection-search") as pool:
            futures = [pool.submit(contextvars.copy_context().run, one, t) for t in targets]
            outcomes = [f.result() for f in futures]
    found: Dict[str, dict] = {}
    for cid, out, error in outcomes:
        if error is not None:
            if on_error is not None:
                on_error(cid, error)
            else:
                logger.warning("Search failed for collection %s: %s", cid, error)
            continue
        found[cid] = out
    return found


def merge(per_collection: Sequence[Tuple[str, Sequence[Any]]], top_k: int) -> List[Hit]:
    """One ranking from several, best first, as ``(result, collection_id)``.

    Every result carries the cross-encoder's score: sort on it. Otherwise
    (no reranker, or one collection's pass was shed under load) sort on the
    first-stage score, as before; see the module note for why not by rank."""
    hits = [(r, cid) for cid, results in per_collection for r in results]
    if not hits:
        return []
    if len(per_collection) == 1:
        return hits[:top_k]
    if all(getattr(r, "rerank_score", None) is not None for r, _ in hits):
        hits.sort(key=lambda h: h[0].rerank_score, reverse=True)
    else:
        hits.sort(key=lambda h: float(h[0].similarity_score), reverse=True)
    return hits[:top_k]


def search_collections(
    targets: Sequence[Tuple[str, Any]],
    query: str,
    top_k: int,
    *,
    on_error: Optional[Callable[[str, Exception], None]] = None,
    **search_kwargs: Any,
) -> List[Hit]:
    """Search each ``(collection_id, indexer)`` and return the best ``top_k``
    across all of them as ``(result, collection_id)`` pairs, best first."""
    found = search_each(targets, query, top_k, on_error=on_error, **search_kwargs)
    order = [cid for cid, _ in targets if cid in found]
    return merge([(cid, found[cid]["results"]) for cid in order], top_k)
