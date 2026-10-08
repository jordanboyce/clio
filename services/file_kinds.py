"""File kinds: the four families a source can belong to.

The sidebar groups and filters sources by what they are (code, documents,
data tables, media) rather than by extension, and the server has to agree
with it on the boundaries. This is the one place that mapping lives; the
frontend mirrors the labels in utils/fileTypes.js but asks the server for
counts and filtering so a paged list stays honest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

KINDS = ("code", "docs", "data", "media")

DOC_EXTENSIONS = {".pdf", ".txt", ".docx", ".md", ".html", ".htm"}
DATA_EXTENSIONS = {".csv", ".xlsx", ".xls", ".json", ".jsonl"}


def _code_extensions() -> set:
    from services.code_extractor import SUPPORTED_CODE_EXTENSIONS
    return set(SUPPORTED_CODE_EXTENSIONS)


def _code_basenames() -> set:
    # Extension-less files the code extractor knows by name (Makefile,
    # Dockerfile, ...). Optional: older builds of the extractor have no table.
    try:
        from services.code_extractor import CODE_FILENAMES
        return {name.lower() for name in CODE_FILENAMES}
    except ImportError:
        return set()


def _media_extensions() -> set:
    from services.document_extractor import AUDIO_EXTENSIONS, DocumentExtractor
    return set(AUDIO_EXTENSIONS) | set(DocumentExtractor.IMAGE_EXTENSIONS)


def extensions_for_kind(kind: str) -> set:
    """Lowercase suffixes (and bare basenames for code) that belong to `kind`."""
    if kind == "code":
        return _code_extensions() | _code_basenames()
    if kind == "docs":
        return set(DOC_EXTENSIONS)
    if kind == "data":
        return set(DATA_EXTENSIONS)
    if kind == "media":
        return _media_extensions()
    return set()


def kind_for_filename(filename: str) -> Optional[str]:
    """Which family a filename belongs to, or None when it is not indexable."""
    name = Path(filename or "").name
    suffix = Path(name).suffix.lower()
    if suffix in DOC_EXTENSIONS:
        return "docs"
    if suffix in DATA_EXTENSIONS:
        return "data"
    if suffix in _media_extensions():
        return "media"
    if suffix in _code_extensions() or name.lower() in _code_basenames():
        return "code"
    return None


def kind_key_for_filename(filename: str) -> str:
    """The grouping key the store aggregates on: the suffix, or the bare
    name for extension-less files. Matches the SQL expression in
    MetadataStore.count_documents_by_key so Python and SQLite agree."""
    name = Path(filename or "").name
    if "." in name and not name.startswith(".") or name.count(".") > 1:
        return name[name.rfind("."):].lower()
    return name.lower()


def kind_counts(key_counts: Dict[str, int]) -> Dict[str, int]:
    """Fold per-key counts (from the store) into per-kind counts."""
    totals: Dict[str, int] = {k: 0 for k in KINDS}
    other = 0
    for key, count in key_counts.items():
        kind = kind_for_filename(key if key.startswith(".") else key)
        if kind is None:
            # A bare suffix like ".py" is not a filename; try it as one.
            kind = kind_for_filename(f"x{key}") if key.startswith(".") else None
        if kind:
            totals[kind] += int(count)
        else:
            other += int(count)
    if other:
        totals["other"] = other
    return totals


def keys_for_kind(kind: str) -> List[str]:
    """The grouping keys (suffixes / bare names) that select `kind` in SQL."""
    return sorted(extensions_for_kind(kind))
