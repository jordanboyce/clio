"""Descriptive filenames for papers.

Downloaded papers arrive as ``2104.08663v2.pdf``, ``nihms-123456.pdf`` or
``s41586-021-03819-2.pdf``. A researcher looking for a paper remembers who
wrote it, roughly when, and what it was about, so the suggested name is built
from exactly those fields — by default ``2021 - Smith et al - Sparse
Retrieval Improves Grounded Question Answering.pdf``.

A name is only suggested from fields the research record holds (read from
the paper or a registry, see ``extract``/``lookup``); with no title there is
no suggestion, because a name made up for a paper is worse than its
download name. Renaming keeps the original name on the research record so
it can be restored.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
from typing import List, Optional

from services.research.store import now_iso

logger = logging.getLogger(__name__)

DEFAULT_PATTERN = "{year} - {authors} - {title}"
PATTERN_FIELDS = ("year", "authors", "first_author", "title", "venue", "doi")
MAX_TITLE_CHARS = 90
MAX_NAME_CHARS = 150

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def _surname(name: str) -> str:
    name = " ".join((name or "").split())
    if "," in name:  # "Smith, Jane"
        return name.split(",", 1)[0].strip()
    parts = name.split()
    if not parts:
        return ""
    # Keep particles with the surname: "de la Cruz", "van der Berg".
    particles = {"van", "von", "de", "der", "da", "di", "le", "la", "del", "den", "du", "dos", "ter"}
    i = len(parts) - 1
    while i > 0 and parts[i - 1].lower() in particles:
        i -= 1
    return " ".join(parts[i:])


def authors_label(authors: List[str]) -> str:
    surnames = [s for s in (_surname(a) for a in authors or []) if s]
    if not surnames:
        return ""
    if len(surnames) == 1:
        return surnames[0]
    if len(surnames) == 2:
        return f"{surnames[0]} & {surnames[1]}"
    return f"{surnames[0]} et al"


_SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into", "nor", "of",
                "on", "or", "the", "to", "with", "vs", "via"}


def _readable_case(title: str) -> str:
    """Books often print their title in capitals; a file name in capitals is
    hard to scan. Only an all-capitals title is recased — the words stay."""
    letters = [c for c in title if c.isalpha()]
    if not letters or not all(c.isupper() for c in letters):
        return title
    words = title.lower().split()
    return " ".join(w if i and w in _SMALL_WORDS else w[:1].upper() + w[1:] for i, w in enumerate(words))


def _shorten(title: str, limit: int) -> str:
    title = " ".join((title or "").split()).rstrip(" .")
    if len(title) <= limit:
        return title
    # Cut at a subtitle first ("Title: a long subtitle"), else a word boundary.
    head = re.split(r"\s*[:—–]\s+", title, maxsplit=1)[0]
    if 20 <= len(head) <= limit:
        return head
    cut = title[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-")


def sanitize(name: str) -> str:
    name = unicodedata.normalize("NFC", name or "")
    name = _UNSAFE.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip(" .-")
    # Collapse separators left dangling by an empty field: "2021 -  - Title".
    name = re.sub(r"(?:\s*-\s*){2,}", " - ", name).strip(" -")
    return name


def suggest_filename(paper: Optional[dict], current_filename: str,
                     pattern: str = DEFAULT_PATTERN) -> Optional[str]:
    """The descriptive name for a paper, keeping its extension; None when
    the record has no title to build one from."""
    if not paper or not (paper.get("title") or "").strip():
        return None
    authors = paper.get("authors") or []
    fields = {
        "year": str(paper.get("year") or ""),
        "authors": authors_label(authors),
        "first_author": _surname(authors[0]) if authors else "",
        "title": _shorten(_readable_case(paper.get("title") or ""), MAX_TITLE_CHARS),
        "venue": " ".join((paper.get("venue") or "").split())[:40],
        "doi": (paper.get("doi") or "").replace("/", "_"),
    }
    try:
        stem = (pattern or DEFAULT_PATTERN).format(**fields)
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError(
            f"Unknown field in naming pattern ({exc}). Use: "
            + ", ".join("{" + f + "}" for f in PATTERN_FIELDS)
        ) from exc
    stem = sanitize(stem)[:MAX_NAME_CHARS].rstrip(" .-")
    if not stem:
        return None
    suffix = Path(current_filename or "").suffix.lower()
    return f"{stem}{suffix}"


def _available_name(directory: Path, wanted: str, current: str) -> str:
    """``wanted``, or ``wanted (2)`` … when another file already has it."""
    if wanted == current or not (directory / wanted).exists():
        return wanted
    stem, suffix = Path(wanted).stem, Path(wanted).suffix
    for n in range(2, 1000):
        candidate = f"{stem} ({n}){suffix}"
        if candidate == current or not (directory / candidate).exists():
            return candidate
    raise ValueError("Too many documents share that name")


def rename_document(collection_id: str, indexer, document_id: str, new_name: str,
                    store=None) -> dict:
    """Rename a document: its stored file, its record and its chunks.

    A local reference (a file indexed in place from the user's own folder)
    keeps its file where and as it is — only the name Clio shows changes.
    Returns ``{"document_id", "filename", "previous_filename", "file_moved"}``.
    """
    from services.collection_service import collection_service

    metadata = indexer.vector_store.metadata_store
    info = metadata.get_document_info(document_id)
    if not info:
        raise KeyError(document_id)
    current = info["filename"]
    new_name = sanitize(new_name or "")
    if not new_name:
        raise ValueError("The new name is empty")
    current_suffix = Path(current).suffix.lower()
    if Path(new_name).suffix.lower() != current_suffix:
        # The extension decides how the file is served and re-extracted.
        new_name = f"{new_name}{current_suffix}"
    if len(new_name) > MAX_NAME_CHARS + 10:
        raise ValueError(f"Names are limited to {MAX_NAME_CHARS} characters")

    file_moved = False
    if info.get("source_type") == "local_reference":
        final = new_name
    else:
        directory = collection_service.get_documents_path(collection_id)
        final = _available_name(directory, new_name, current)
        source = directory / current
        target = directory / final
        if final != current and source.exists():
            source.rename(target)
            file_moved = True

    try:
        if not metadata.rename_document(document_id, final):
            raise KeyError(document_id)
    except Exception:
        if file_moved:  # keep the file and the record in agreement
            (directory / final).rename(directory / current)
        raise

    if store is not None and final != current:
        store.upsert_paper(document_id, original_filename=current, renamed_at=now_iso())
    # Cached answers cite documents by name; they would show the old one.
    try:
        from services.answer_cache import answer_cache
        answer_cache.clear(f"col:{collection_id}")
    except Exception as exc:  # noqa: BLE001 - a stale cache is cosmetic
        logger.debug("Answer cache not cleared after rename: %s", exc)
    return {"document_id": document_id, "filename": final,
            "previous_filename": current, "file_moved": file_moved}
