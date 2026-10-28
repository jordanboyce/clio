"""JSON-Schema tool specs for the agentic chat loop.

One canonical list of tools. Each provider's `complete_with_tools` consumes
either `anthropic_tools()` or `openai_tools()` — the underlying dispatch is
shared via `services.structured_chat.execute_tool_calls`.
"""

from __future__ import annotations

from typing import Any, Dict, List


# Canonical tool list. `parameters` is JSON Schema (draft-7-compatible).
_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "research_documents",
        "description": (
            "Research a comparison or multi-part question using multiple focused "
            "queries, hybrid/keyword/literal retrieval, rank fusion and document "
            "diversity. Returns bounded passages, coverage gaps and follow-up "
            "context arguments. Supply subqueries for separate requirements, "
            "exceptions or effective dates. Coverage is not answer confidence."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 1000},
                "subqueries": {"type": "array", "maxItems": 3, "items": {"type": "string", "minLength": 1, "maxLength": 1000}},
                "collection_id": {"type": "string"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                "max_per_document": {"type": "integer", "minimum": 1, "maximum": 10},
                "max_context_chars": {"type": "integer", "minimum": 1000, "maximum": 40000,
                                      "description": "Total excerpt characters, excluding JSON metadata."},
                "filters": {"type": "object", "description": "Same document_ids, filenames, source_formats and upload date filters as search_documents."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "find_in_documents",
        "description": "Find a literal phrase, policy ID or code identifier in indexed text. Preserves punctuation. Returns matching passages and chunk anchors for context; use research_documents for conceptual questions.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "minLength": 1, "maxLength": 1000},
                "case_sensitive": {"type": "boolean"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
                "collection_id": {"type": "string"},
                "filters": {"type": "object", "description": "Same metadata filters as search_documents."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "search_documents",
        "description": (
            "Search the active document collection. Meaning and exact keywords "
            "are balanced automatically per query, so leave `mode` unset unless "
            "an earlier search missed. Use for narrative, prose, or conceptual questions "
            "about PDFs, text, or code. Returns ranked chunks with filename, "
            "page number, and a similarity score. Pass `filters` to restrict "
            "retrieval to specific documents, file types, or a date range — "
            "this sharply improves precision on large collections."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language query or exact keywords."},
                "mode": {"type": "string", "enum": ["auto", "semantic", "keyword", "hybrid"]},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                "collection_id": {"type": "string", "description": "Omit to use the active chat collection."},
                "filters": {
                    "type": "object",
                    "description": (
                        "Optional metadata pre-filter. Discover valid values via "
                        "get_collection_info. All keys are AND-combined."
                    ),
                    "properties": {
                        "document_ids": {"type": "array", "items": {"type": "string"}},
                        "filenames": {"type": "array", "items": {"type": "string"}},
                        "source_formats": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "File types, e.g. [\"pdf\", \"csv\"].",
                        },
                        "date_from": {"type": "string", "description": "ISO timestamp lower bound on upload time."},
                        "date_to": {"type": "string", "description": "ISO timestamp upper bound on upload time."},
                        "include_superseded": {
                            "type": "boolean",
                            "description": (
                                "True to rank documents marked as replaced like current ones "
                                "(for 'what did the old version say'). Default: they rank "
                                "lower but still appear, labelled superseded_by."
                            ),
                        },
                    },
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_document_context",
        "description": (
            "Fetch the full text of a document, a page, or a chunk neighborhood. "
            "Use after search_documents when you need more than the truncated excerpt."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "page_number": {"type": "integer"},
                "chunk_id": {"type": "string"},
                "window": {"type": "integer", "description": "Chunks before/after when chunk_id is given."},
                "max_chars": {"type": "integer"},
                "collection_id": {"type": "string"},
            },
            "required": ["document_id"],
        },
    },
    {
        "name": "list_tables",
        "description": (
            "List every CSV/Excel sheet ingested as a typed SQL table. Call this "
            "first when a question requires numeric/aggregation/filtering/ranking "
            "over tabular data."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "collection_id": {"type": "string"},
            },
        },
    },
    {
        "name": "get_table_schema",
        "description": (
            "Return the full typed schema of an ingested CSV/Excel sheet, "
            "including column sql_names, types, detected column roles, "
            "sample values, and stats."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "identifier": {"type": "string", "description": "table_name, filename, or document_id."},
                "collection_id": {"type": "string"},
            },
            "required": ["identifier"],
        },
    },
    {
        "name": "get_table_rows",
        "description": (
            "Return the full rows of an ingested table in one call (no SQL). "
            "Best for small/medium tables up to ~2000 rows."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "identifier": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
                "collection_id": {"type": "string"},
            },
            "required": ["identifier"],
        },
    },
    {
        "name": "query_table",
        "description": (
            "Run a read-only SQL SELECT against the typed CSV/Excel tables. "
            "Rules: SELECT/WITH only, single statement, double-quote every "
            "identifier (column names are case-sensitive — use the exact sql_name)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
                "max_rows": {"type": "integer", "minimum": 1, "maximum": 2000},
                "collection_id": {"type": "string"},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "aggregate_table",
        "description": "Group-by aggregation on a table without writing SQL.",
        "parameters": {
            "type": "object",
            "properties": {
                "identifier": {"type": "string"},
                "aggregate_col": {"type": "string"},
                "agg_fn": {"type": "string", "enum": ["sum", "mean", "count", "min", "max"]},
                "group_by": {"type": "string"},
                "sort_by": {"type": "string", "enum": ["value_asc", "value_desc", "group_asc", "group_desc"]},
                "limit": {"type": "integer"},
                "collection_id": {"type": "string"},
            },
            "required": ["identifier", "aggregate_col", "agg_fn"],
        },
    },
    {
        "name": "list_collections",
        "description": "List every available collection (id, name, document count).",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_collection_stats",
        "description": (
            "Exact statistics for a collection: total documents, pages, chunks, bytes, "
            "upload date range, counts per file extension and kind, largest files. Use for "
            "any 'how many', 'how big', 'what types' question. Never count from search results."
        ),
        "parameters": {"type": "object", "properties": {"collection_id": {"type": "string"}}},
    },
    {
        "name": "list_documents",
        "description": (
            "Page through the files in a collection (name, type, bytes, pages, upload date). "
            "Returns total_matching and next_offset; call again with next_offset to continue. "
            "Filter by name_contains or file_type (an extension such as pdf, or docs/code/data/"
            "media/other); sort by newest, oldest, name, name_desc, largest or smallest."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "collection_id": {"type": "string"},
                "limit": {"type": "integer", "description": "Page size, 1-500 (default 100)"},
                "offset": {"type": "integer"},
                "name_contains": {"type": "string"},
                "file_type": {"type": "string"},
                "sort": {"type": "string",
                         "enum": ["newest", "oldest", "name", "name_desc", "largest", "smallest"]},
            },
        },
    },
    {
        "name": "get_collection_info",
        "description": "Description, guide and the newest documents of a collection.",
        "parameters": {
            "type": "object",
            "properties": {
                "collection_id": {"type": "string"},
            },
        },
    },
]


def anthropic_tools() -> List[Dict[str, Any]]:
    """Shape tools for Anthropic's messages API (`tools` parameter)."""
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["parameters"],
        }
        for t in _TOOLS
    ]


def openai_tools() -> List[Dict[str, Any]]:
    """Shape tools for OpenAI's chat.completions API (`tools` parameter)."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            },
        }
        for t in _TOOLS
    ]


def tool_names() -> List[str]:
    return [t["name"] for t in _TOOLS]
