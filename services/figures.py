"""Stored figures: where a document's extracted pictures live and how
results point at them.

``services.figure_extractor`` pulls the pictures out of a file; this module
keeps them. Each collection has a ``figures/`` folder beside ``documents/``
holding ``<document_id>/<n>.<ext>``, and the collection's metadata.db has a
``document_figures`` row per file (page, order on the page, size, caption,
optional vision description). The folder path is derived from the metadata
database's location, so the indexer, the serving endpoint and the bundle
exporter agree on it without being told.

Figures follow their document: they are written when it is indexed,
replaced when it is re-indexed, removed when it is deleted, counted in the
collection's storage, and travel in collection exports. They are served
only through the same quarantine check as the file itself.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from models.schemas import ChunkMetadata
from services.figure_extractor import CAPTION_RE, ExtractedFigure, media_type_for

logger = logging.getLogger(__name__)

FIGURES_DIRNAME = "figures"
# Chunks that carry a figure's description are tagged with this method, so
# the UI can say "figure" rather than "text" and a search can tell them apart.
FIGURE_EXTRACTION_METHOD = "figure"
# Vision calls multiply at index time; a deck of a hundred photographs
# gets its first forty described and the rest shown without words.
MAX_DESCRIBED_FIGURES_PER_DOCUMENT = 40
_CONTEXT_DESCRIPTION_CHARS = 160


def figures_root(metadata_store) -> Path:
    """The collection's figures folder: beside ``documents/`` and ``indexes/``."""
    return Path(metadata_store.db_path).parent.parent / FIGURES_DIRNAME


def figure_url(base_url: str, document_id: str, collection_id: str, figure_number: int) -> str:
    return f"{base_url}/documents/{document_id}/figures/{figure_number}?collection_id={collection_id}"


def store_figures(metadata_store, root: Path, document_id: str,
                  figures: Sequence[ExtractedFigure]) -> List[Dict[str, Any]]:
    """Write a document's figures to disk and record them, replacing any
    earlier set. Returns the rows recorded."""
    folder = root / document_id
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
    if not figures:
        metadata_store.replace_figures(document_id, [])
        return []
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for number, figure in enumerate(figures, start=1):
        name = f"{number}.{figure.ext}"
        (folder / name).write_bytes(figure.data)
        rows.append({
            "document_id": document_id,
            "figure_number": number,
            "page_number": int(figure.page_number),
            "position": int(figure.position),
            "path": f"{document_id}/{name}",
            "width": int(figure.width),
            "height": int(figure.height),
            "byte_size": len(figure.data),
            "content_hash": figure.content_hash or None,
            "caption": figure.caption or None,
            "description": figure.description or None,
        })
    metadata_store.replace_figures(document_id, rows)
    logger.info(f"Stored {len(rows)} figure(s) for document {document_id}")
    return rows


def delete_figures(metadata_store, root: Path, document_id: str) -> None:
    """Remove a document's figure rows and files. Safe to repeat."""
    try:
        metadata_store.delete_figures(document_id)
    except Exception as e:  # the rows already went with the document
        logger.debug(f"figure rows for {document_id} not removed: {e}")
    shutil.rmtree(root / document_id, ignore_errors=True)


def clear_figures(root: Path) -> None:
    """Remove every stored figure of a collection (full re-index, clear)."""
    shutil.rmtree(root, ignore_errors=True)


def resolve_figure_file(root: Path, row: Dict[str, Any]) -> Optional[Path]:
    """The file behind a figure row, or None when it is missing or the
    recorded path would leave the figures folder."""
    rel = str(row.get("path") or "")
    if not rel or rel.startswith(("/", "\\")) or ".." in Path(rel).parts or ":" in rel:
        return None
    root_resolved = root.resolve()
    path = (root / rel).resolve()
    try:
        path.relative_to(root_resolved)
    except ValueError:
        return None
    return path if path.is_file() else None


def etag_for(path: Path) -> str:
    st = path.stat()
    ident = (str(path.resolve()), st.st_mtime_ns, st.st_size)
    return '"' + hashlib.sha1(repr(ident).encode("utf-8")).hexdigest()[:32] + '"'


def media_type(row: Dict[str, Any]) -> str:
    return media_type_for(Path(str(row.get("path") or "")).suffix)


# ── Describing figures ──────────────────────────────────────────────────


def describe_figures(figures: Sequence[ExtractedFigure], engine, filename: str = "") -> int:
    """Ask the configured vision engine what each figure shows. Returns how
    many were described; a failing call leaves that figure without words."""
    if engine is None or not figures:
        return 0
    described = 0
    for figure in figures[:MAX_DESCRIBED_FIGURES_PER_DOCUMENT]:
        try:
            text = engine.describe_image_bytes(figure.data, figure.media_type)
        except Exception as e:
            logger.warning(
                f"Figure description failed for {filename or 'document'} page {figure.page_number}: {e}"
            )
            continue
        if text and text.strip():
            figure.description = text.strip()
            described += 1
    return described


def description_chunks(document_id: str, filename: str, source_format: Optional[str],
                       figures: Sequence[ExtractedFigure]) -> List[ChunkMetadata]:
    """One chunk per described figure, so a chart is findable by what it shows."""
    chunks = []
    for number, figure in enumerate(figures, start=1):
        if not figure.description:
            continue
        # A caption that already reads "Figure 3: …" is the heading; any
        # other caption is named after the figure's number here.
        if figure.caption and CAPTION_RE.match(figure.caption):
            heading = figure.caption
        else:
            heading = f"Figure {number}" + (f": {figure.caption}" if figure.caption else "")
        chunks.append(ChunkMetadata(
            chunk_id=f"{document_id}_p{figure.page_number}_f{number}",
            document_id=document_id,
            filename=filename,
            page_number=int(figure.page_number),
            chunk_index=1000 + number,  # after the page's text chunks
            text=f"{heading}\n{figure.description}",
            source_format=source_format,
            extraction_method=FIGURE_EXTRACTION_METHOD,
        ))
    return chunks


# ── Figures on cited pages ──────────────────────────────────────────────


def _ref(row: Dict[str, Any], base_url: str, collection_id: str) -> Dict[str, Any]:
    return {
        "url": figure_url(base_url, row["document_id"], collection_id, int(row["figure_number"])),
        "figure_number": int(row["figure_number"]),
        "page_number": int(row["page_number"]),
        "caption": row.get("caption") or None,
        "width": row.get("width"),
        "height": row.get("height"),
        "description": row.get("description") or None,
    }


def refs_for_pages(metadata_store, base_url: str, collection_id: str,
                   pages: Iterable[Tuple[str, int]]) -> Dict[Tuple[str, int], List[Dict[str, Any]]]:
    """Figure payloads for (document_id, page_number) pairs of one collection."""
    wanted = {(d, int(p)) for d, p in pages if d and p}
    if not wanted:
        return {}
    try:
        rows = metadata_store.get_figures_for_pages(wanted)
    except Exception as e:
        logger.warning(f"figures lookup failed for {collection_id}: {e}")
        return {}
    out: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
    for row in rows:
        out.setdefault((row["document_id"], int(row["page_number"])), []).append(_ref(row, base_url, collection_id))
    return out


def refs_for_sources(base_url: str,
                     sources: Iterable[Tuple[str, str, int]]) -> Dict[Tuple[str, str, int], List[Dict[str, Any]]]:
    """Figure payloads for (collection_id, document_id, page_number) triples
    across collections; a collection that cannot be opened contributes none."""
    from services.indexer_manager import indexer_manager

    by_collection: Dict[str, set] = {}
    for cid, document_id, page in sources:
        if cid and document_id and page:
            by_collection.setdefault(cid, set()).add((document_id, int(page)))
    out: Dict[Tuple[str, str, int], List[Dict[str, Any]]] = {}
    for cid, pages in by_collection.items():
        try:
            store = indexer_manager.get_indexer(cid).vector_store.metadata_store
        except Exception as e:
            logger.debug(f"figures: collection {cid} not opened: {e}")
            continue
        for (document_id, page), refs in refs_for_pages(store, base_url, cid, pages).items():
            out[(cid, document_id, page)] = refs
    return out


def attach_to_results(metadata_store, base_url: str, collection_id: str, results: Iterable[Any]) -> None:
    """Set ``figures`` on each search result for the page it cites."""
    results = list(results)
    refs = refs_for_pages(metadata_store, base_url, collection_id,
                          ((r.document_id, r.page_number) for r in results))
    for r in results:
        r.figures = refs.get((r.document_id, int(r.page_number or 0))) or None


def context_note(refs: Optional[Sequence[Dict[str, Any]]]) -> str:
    """How the chat prompt names a page's figures, so the model can say
    "figure 2 on page 12" and the reader finds it under the source."""
    if not refs:
        return ""
    parts = []
    for ref in refs:
        label = f"figure {ref['figure_number']}"
        if ref.get("caption"):
            label += f' "{ref["caption"]}"'
        description = (ref.get("description") or "").strip()
        if description:
            if len(description) > _CONTEXT_DESCRIPTION_CHARS:
                description = description[:_CONTEXT_DESCRIPTION_CHARS - 1].rstrip() + "…"
            label += f" ({description})"
        parts.append(label)
    return "Figures on this page: " + "; ".join(parts)
