"""The text the search model embeds for each chunk.

The stored and displayed text of a chunk is not always the best thing to
embed: a passage that says "the limit is 30 days" carries nothing about
which policy it belongs to. Every place that embeds chunks for the index
(first indexing, re-indexing, filling in missing vectors) and the retrieval
benchmark go through here, so they cannot drift apart.
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence


def texts_for_embedding(chunks: Sequence[Any], title: Optional[str] = None) -> List[str]:
    """One string per chunk, in order."""
    return [c.text for c in chunks]
