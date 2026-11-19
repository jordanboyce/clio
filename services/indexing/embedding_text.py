"""The text the search model embeds for each chunk, and the indexing recipe
a collection was built with.

The stored and displayed text of a chunk is not always the best thing to
embed: a passage that says "the limit is 30 days" carries nothing about
which policy it belongs to. Every place that embeds chunks for the index
(first indexing, re-indexing, filling in missing vectors) and the retrieval
benchmark go through here, so they cannot drift apart.

Recipes (B2). A collection keeps the recipe it was built with, so a change
here never mixes two kinds of vector in one index:

  1  the chunk text as stored; pages chunked one at a time (every collection
     indexed before recipes existed)
  2  a header line naming the source, then the chunk text; the current
     heading carried from one page to the next and a sentence cut by a page
     break finished on the page it starts on (services/chunker.py)

Keyword search and what people read stay on the stored text in both.
"""

from __future__ import annotations

import logging
import re
from typing import Any, List, Optional, Sequence

logger = logging.getLogger(__name__)

PLAIN = 1
HEADED = 2
CURRENT_RECIPE = HEADED

_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,8}$")
_SEPARATORS = re.compile(r"[-_\s]+")


def title_from_filename(filename: str) -> str:
    """"harbor-travel_policy rev-c.pdf" -> "harbor travel policy rev c"."""
    stem = _EXTENSION.sub("", str(filename or "").replace("\\", "/").rsplit("/", 1)[-1])
    return _SEPARATORS.sub(" ", stem).strip()


def _field(chunk: Any, name: str) -> Any:
    return chunk.get(name) if isinstance(chunk, dict) else getattr(chunk, name, None)


def texts_for_embedding(chunks: Sequence[Any], title: Optional[str] = None,
                        recipe: int = CURRENT_RECIPE) -> List[str]:
    """One string per chunk, in order. ``chunks`` may be ChunkMetadata or
    stored rows (dicts with ``text`` and ``filename``). ``title`` names the
    source; by default it comes from each chunk's filename, which is what a
    re-index of stored rows has too."""
    out: List[str] = []
    for c in chunks:
        text = _field(c, "text") or ""
        if recipe < HEADED:
            out.append(text)
            continue
        header = title or title_from_filename(_field(c, "filename") or "")
        out.append(f"{header}\n{text}" if header else text)
    return out


def read_recipe(metadata_store, *, empty: bool) -> int:
    """The recipe a collection's index uses, from the index's own record of
    what produced its vectors (index_info, which travels with every copy of
    the collection). An index with no recorded recipe is recipe 1 if it
    already holds chunks; an empty one takes the current recipe, recorded
    so it sticks."""
    try:
        recorded = metadata_store.get_index_info().get("recipe")
    except Exception as e:  # noqa: BLE001 - unreadable: assume the oldest, change nothing
        logger.warning("Could not read the indexing recipe (%s); indexing as recipe 1", e)
        return PLAIN
    if recorded is not None:
        try:
            return int(recorded)
        except (TypeError, ValueError):
            return PLAIN
    if not empty:
        return PLAIN
    write_recipe(metadata_store, CURRENT_RECIPE)
    return CURRENT_RECIPE


def write_recipe(metadata_store, recipe: int) -> None:
    try:
        metadata_store.set_index_info({"recipe": int(recipe)})
    except Exception as e:  # noqa: BLE001 - indexing goes on; the next start decides again
        logger.warning("Could not record the indexing recipe: %s", e)
