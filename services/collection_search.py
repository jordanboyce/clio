"""Search several collections and merge the results into one ranking.

Asking across all collections (Ask with the scope set to all, the MCP tool
search_all_collections, the benchmark's cross-collection questions) comes
through here, so the merge is one piece of code with one test.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

Hit = Tuple[Any, str]


def search_collections(
    targets: Sequence[Tuple[str, Any]],
    query: str,
    top_k: int,
    *,
    on_error: Optional[Callable[[str, Exception], None]] = None,
    **search_kwargs: Any,
) -> List[Hit]:
    """Search each ``(collection_id, indexer)`` and return the best ``top_k``
    as ``(result, collection_id)`` pairs, best first.

    A collection whose search fails is left out (``on_error`` is told)."""
    hits: List[Hit] = []
    for cid, indexer in targets:
        try:
            found = indexer.search(query=query, top_k=top_k, **search_kwargs)
        except Exception as e:  # noqa: BLE001 - one bad collection must not fail the rest
            if on_error is not None:
                on_error(cid, e)
            else:
                logger.warning("Search failed for collection %s: %s", cid, e)
            continue
        hits.extend((r, cid) for r in found["results"])
    hits.sort(key=lambda h: h[0].similarity_score, reverse=True)
    return hits[:top_k]
