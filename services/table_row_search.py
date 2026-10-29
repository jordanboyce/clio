"""Spreadsheet rows in keyword search.

CSV and XLSX files are stored as typed SQL tables only — never chunked or
embedded — so totals and rankings are computed, not guessed from passages.
That left their rows reachable only through a model's table tools: with no
AI provider connected, nothing in a spreadsheet could be found. This module
gives the Search surface exact row matches straight from those tables, so a
customer name, an invoice number or a SKU is findable with no model at all.

The rows are added to the search response only; the passage index, chat and
MCP retrieval are unchanged.
"""

import logging
from typing import List, Optional

from models.schemas import SearchResult
from services.bm25_service import BM25Index

logger = logging.getLogger(__name__)

# Question words BM25's stopword list keeps but no cell is ever looking for.
# A row must contain every remaining term, so "how many copper kettles are in
# stock" has to reduce to "copper kettles stock" before it can find anything.
_QUESTION_WORDS = {
    "many", "much", "show", "find", "list", "give", "tell", "get", "me",
    "do", "does", "did", "there", "any", "our", "my", "we", "you", "your",
    "about", "please", "look", "up", "search", "whose", "whom",
}


def _allowed_documents(metadata_store, document_ids, filters: Optional[dict]) -> set:
    """The tabular documents a search may return: servable and inside `filters`."""
    hidden = metadata_store.get_hidden_document_ids()
    filters = filters or {}
    wanted_ids = set(filters.get("document_ids") or []) or None
    names = {n.lower() for n in (filters.get("filenames") or [])} or None
    formats = {f.lower().lstrip(".") for f in (filters.get("source_formats") or [])} or None
    date_from, date_to = filters.get("date_from"), filters.get("date_to")

    allowed = set()
    for document_id in document_ids:
        if document_id in hidden or (wanted_ids is not None and document_id not in wanted_ids):
            continue
        if names or formats or date_from or date_to:
            info = metadata_store.get_document_info(document_id) or {}
            uploaded = info.get("upload_timestamp") or ""
            if names is not None and (info.get("filename") or "").lower() not in names:
                continue
            if formats is not None and (info.get("source_format") or "").lower() not in formats:
                continue
            if (date_from and uploaded < date_from) or (date_to and uploaded > date_to):
                continue
        allowed.add(document_id)
    return allowed


def row_results(indexer, query: str, limit: int, filters: Optional[dict] = None) -> List[SearchResult]:
    """Spreadsheet rows containing every keyword of `query`, as search results.

    Never raises: a search still answers with its passages if the table
    store cannot be read.
    """
    terms = [t for t in BM25Index._tokenize(query) if t not in _QUESTION_WORDS]
    if not terms or limit <= 0:
        return []
    try:
        store = indexer.vector_store.structured_store
        metadata_store = indexer.vector_store.metadata_store
        tables = store.list_tables()
        if not tables:
            return []
        allowed = _allowed_documents(
            metadata_store, {t["document_id"] for t in tables}, filters)
        if not allowed:
            return []
        rows = store.find_rows(terms, limit=limit, document_ids=allowed)
    except Exception as e:  # noqa: BLE001 - never fail a search over this
        logger.warning(f"Spreadsheet row search skipped: {e}")
        return []

    results = []
    for row in rows:
        values = {k: (v if isinstance(v, (int, float)) else str(v)) for k, v in row["values"].items()}
        snippet = " | ".join(f"{k}: {v}" for k, v in values.items() if v != "")
        fmt = row["filename"].rsplit(".", 1)[-1].lower() if "." in row["filename"] else "csv"
        results.append(SearchResult(
            filename=row["filename"],
            page_number=1,
            text_snippet=snippet,
            # Every keyword is in the row: an exact match, not a similarity.
            similarity_score=1.0,
            document_id=row["document_id"],
            chunk_id=f"{row['document_id']}_row{row['row_number']}",
            pdf_url="",
            page_url="",
            source_format=fmt,
            extraction_method="text",
            csv_row_number=row["row_number"],
            csv_columns=row["columns"],
            csv_values=values,
        ))
    return results


def merge(passages: List[SearchResult], rows: List[SearchResult], top_k: int) -> List[SearchResult]:
    """Exact row matches first, then passages, `top_k` in all.

    Rows take at most half the list unless passages cannot fill it, so a
    spreadsheet with hundreds of matching rows never crowds every document
    out of the results.
    """
    if not rows:
        return passages
    room = max(top_k // 2, top_k - len(passages), 1)
    return (rows[:room] + passages)[:top_k]
