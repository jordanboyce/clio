"""The chat agent's tools: dispatch, source-selection enforcement, and the
structured-table prompt blocks.

Providers with native tool calling (Anthropic, OpenAI family) receive the
JSON-Schema specs in `services.agent_tools`; providers without it (Ollama)
get the prose protocol from `build_tool_use_instructions` and their
`<tool_call>{...}</tool_call>` blocks are parsed by `parse_tool_calls`.
Either way every call lands in `execute_tool_calls`, which runs the
in-process MCP tool functions against the per-collection stores.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from services.structured_store import SQLValidationError, StructuredStore

logger = logging.getLogger(__name__)


_TOOL_CALL_RE = re.compile(
    r'<tool_call>\s*(\{.*?\})\s*</tool_call>',
    re.DOTALL,
)

_MAX_STRUCTURED_ROWS_PER_TOOL = 200
_MAX_SCHEMA_PROMPT_COLS = 40

SUPPORTED_TOOLS = {
    "research_documents",
    "find_in_documents",
    "query_table",
    "search_documents",
    "get_document_context",
    "list_tables",
    "get_table_schema",
    "get_table_rows",
    "aggregate_table",
    "list_collections",
    "get_collection_info",
    "get_collection_stats",
    "list_documents",
}


def describe_tables_for_prompt(tables: List[Dict[str, Any]], max_tables: int = 20) -> str:
    """Render table schemas into an unambiguous block for the prompt.

    The `sql_table_name` field is the EXACT string the model should use as
    the SQL identifier and as the `identifier` argument on table tool calls.
    Every other field on each entry is metadata — do not concatenate fields
    to form an identifier.
    """
    if not tables:
        return ""

    lines = [
        "You have access to the following typed SQL tables built from the user's "
        "CSV / Excel files. Use them for any numeric, aggregation, filtering, or "
        "ranking question — raw SQL over these tables is dramatically more "
        "accurate than reading row text snippets.",
        "",
        "IDENTIFIER RULE: For every table below, the `sql_table_name` field is "
        "the EXACT string to use in SQL FROM clauses and as the `identifier` "
        "argument to table tools. Never modify it, never combine it with the "
        "`source_file`, never use the filename as the SQL identifier.",
        "",
    ]
    for t in tables[:max_tables]:
        lines.append(f"- sql_table_name: {t['table_name']}")
        lines.append(f"  source_file: {t['filename']}")
        if t.get('sheet_name'):
            lines.append(f"  sheet_name: {t['sheet_name']}")
        lines.append(f"  rows: {t['row_count']}, columns: {t['column_count']}")
        cols = t.get('columns', [])[:_MAX_SCHEMA_PROMPT_COLS]
        if cols:
            lines.append("  columns:")
            for c in cols:
                role = f" [role: {c['role']}]" if c.get('role') else ""
                original = (
                    f"  original: {c['name']}"
                    if c.get('name') and c['name'] != c['sql_name']
                    else ""
                )
                lines.append(
                    f"    - \"{c['sql_name']}\" ({c['type']}){role}{original}"
                )
        if len(t.get('columns', [])) > _MAX_SCHEMA_PROMPT_COLS:
            lines.append(f"    ... ({len(t['columns']) - _MAX_SCHEMA_PROMPT_COLS} more columns)")
        lines.append("")
    if len(tables) > max_tables:
        lines.append(f"... and {len(tables) - max_tables} more tables (call "
                     f"list_tables in MCP to see all).")
    return "\n".join(lines)


def build_tool_use_instructions() -> str:
    """The prose tool protocol for providers without native tool calling.

    Describes every tool the chat loop can dispatch; the model answers with
    `<tool_call>{...}</tool_call>` blocks that `parse_tool_calls` extracts.
    """
    return (
        "TOOL USE PROTOCOL:\n"
        "You are an agent. You may call tools to retrieve documents, run "
        "structured queries against ingested CSV/XLSX tables, or enumerate "
        "collections. Call tools whenever the answer requires data you don't "
        "already have in this prompt — do not guess.\n\n"
        "CRITICAL: Do NOT narrate what you are about to do (\"Let me query...\", "
        "\"I'll look this up...\"). Either emit an actual <tool_call> block, "
        "OR write the final answer. Prose that only announces intent without "
        "an accompanying tool_call is a bug. If you need data, emit the "
        "tool_call block immediately with no preamble.\n\n"
        "To call a tool, emit a JSON block wrapped in <tool_call>...</tool_call> "
        "on its own line. You may emit multiple blocks in one turn to call "
        "tools in parallel. After tool results come back you'll get another "
        "turn to either call more tools or produce the final answer.\n\n"
        "DOCUMENT RETRIEVAL:\n"
        '  - research_documents — multi-query research with bounded evidence and coverage gaps. Use for comparisons, exceptions and multi-part questions.\n'
        '    <tool_call>{"tool":"research_documents","query":"What does EQ-17 require?","subqueries":["EQ-17 exceptions","EQ-17 effective dates"],"top_k":8}</tool_call>\n'
        '  - find_in_documents — literal phrase or identifier lookup (not regex).\n'
        '    <tool_call>{"tool":"find_in_documents","pattern":"EQ-17","max_results":10}</tool_call>\n'
        '  - search_documents — search of the collection (meaning and exact '
        'keywords are balanced automatically; omit "mode"). Use for narrative / '
        'prose / conceptual questions.\n'
        '    <tool_call>{"tool": "search_documents", "query": "Q3 revenue commentary", "top_k": 5}</tool_call>\n'
        '  - get_document_context — fetch the full text of a document, page, '
        'or chunk neighborhood. Use after search_documents to read more than '
        'the truncated excerpt.\n'
        '    <tool_call>{"tool": "get_document_context", "document_id": "<uuid>", "page_number": 3, "window": 1}</tool_call>\n\n'
        "STRUCTURED TABLES (CSV / XLSX ingested as typed SQL tables):\n"
        '  - list_tables — enumerate available tables. Call FIRST when a '
        'question involves CSV/XLSX data you don\'t already see inlined.\n'
        '    <tool_call>{"tool": "list_tables"}</tool_call>\n'
        '  - get_table_schema — full typed schema (columns, types, '
        'detected column roles, sample values, stats).\n'
        '    <tool_call>{"tool": "get_table_schema", "identifier": "data.csv"}</tool_call>\n'
        '  - get_table_rows — return the full rows of a small/medium table '
        'in one call (no SQL needed).\n'
        '    <tool_call>{"tool": "get_table_rows", "identifier": "data.csv", "limit": 200}</tool_call>\n'
        '  - aggregate_table — group-by aggregation without writing SQL.\n'
        '    <tool_call>{"tool": "aggregate_table", "identifier": "data.csv", "aggregate_col": "amount", "agg_fn": "sum", "group_by": "category", "sort_by": "value_desc"}</tool_call>\n'
        '  - query_table — read-only SQL SELECT for ad-hoc analytics.\n'
        '    <tool_call>{"tool": "query_table", "sql": "SELECT \\"category\\", SUM(\\"amount\\") FROM \\"csv_data_abc\\" GROUP BY \\"category\\""}</tool_call>\n'
        '    Rules: SELECT/WITH only, single statement, double-quote every '
        'identifier, column names are case-sensitive (use the exact sql_name).\n\n'
        "COLLECTION META:\n"
        '  - list_collections — list every available collection (id, name, '
        'doc count). Use when the user references a different collection.\n'
        '    <tool_call>{"tool": "list_collections"}</tool_call>\n'
        '  - get_collection_stats — EXACT totals: documents, pages, chunks, bytes, '
        'upload date range, counts per file type, largest files. Use it for any '
        '"how many / how big / what types" question; never count from search results.\n'
        '    <tool_call>{"tool": "get_collection_stats"}</tool_call>\n'
        '  - list_documents — page through the files themselves (names, sizes, dates). '
        'Args: limit (max 500), offset, name_contains, file_type (pdf, docs, code, data, '
        'media, other), sort (newest, oldest, name, name_desc, largest, smallest). '
        'The reply has total_matching and next_offset; keep calling with next_offset to '
        'reach every file. For a complete export of a very large collection, tell the '
        'user the Export files button / GET /documents/export writes the full list as CSV.\n'
        '    <tool_call>{"tool": "list_documents", "file_type": "pdf", "sort": "largest", "limit": 20}</tool_call>\n'
        '  - get_collection_info — description, guide and the newest documents of a collection.\n'
        '    <tool_call>{"tool": "get_collection_info", "collection_id": "..."}</tool_call>\n\n'
        "OUTPUT FORMATTING:\n"
        "- Final answers are rendered as GitHub-flavored markdown. Use "
        "headings, bullets, bold, and tables when they help — but prefer "
        "tight prose for short answers.\n"
        "- When a tool result includes urls, render every link as [Title](url) "
        "using the EXACT `url` field. NEVER use `#`, `(here)`, or any "
        "placeholder href — if a result has no url, omit the link entirely.\n\n"
        "GUIDANCE:\n"
        "- For numeric/aggregation/ranking questions about CSV/XLSX data, "
        "always go through the structured-table tools — never estimate from "
        "search excerpts.\n"
        "- For prose/conceptual questions about PDFs/docs, prefer "
        "search_documents (the prompt may already include retrieved chunks).\n"
        "- When you have everything you need, stop calling tools and write "
        "the final answer. Cite numbers verbatim from tool results; never "
        "invent values the tools didn't return.\n"
        "- Omit `collection_id` to use the active chat collection. Pass an "
        "explicit id only when the user asks about a different collection."
    )


def parse_tool_calls(response_text: str) -> List[Dict[str, Any]]:
    """Extract `<tool_call>...</tool_call>` JSON blocks from the model's output."""
    calls: List[Dict[str, Any]] = []
    for match in _TOOL_CALL_RE.finditer(response_text):
        raw = match.group(1).strip()
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            calls.append({
                "tool": None,
                "raw": raw,
                "error": f"Invalid JSON in tool_call: {e}",
            })
            continue
        if not isinstance(obj, dict):
            calls.append({"tool": None, "raw": raw, "error": "tool_call must be a JSON object"})
            continue
        calls.append(obj)
    return calls


def _resolve_store_for_identifier(
    identifier: Optional[str],
    stores: Dict[str, StructuredStore],
    tables: List[Dict[str, Any]],
) -> Optional[StructuredStore]:
    """Look up the store whose tables contain the given identifier."""
    if not stores:
        return None
    if identifier:
        for t in tables:
            if identifier in (t.get("table_name"), t.get("filename"), t.get("document_id")):
                cid = t.get("collection_id")
                if cid and cid in stores:
                    return stores[cid]
    return next(iter(stores.values()))


def _resolve_store_for_sql(
    sql: str,
    stores: Dict[str, StructuredStore],
    tables: List[Dict[str, Any]],
) -> Optional[StructuredStore]:
    """Scan SQL text for a known table name and route to the owning store."""
    if not stores:
        return None
    for t in tables:
        name = t.get("table_name") or ""
        if name and re.search(rf'\b{re.escape(name)}\b', sql):
            cid = t.get("collection_id")
            if cid and cid in stores:
                return stores[cid]
    return next(iter(stores.values()))


_TOOL_ALIASES = {
    "query_structured_table": "query_table",
    "search": "search_documents",
    "search_collection": "search_documents",
    "document_context": "get_document_context",
}


class _SourceSelection:
    """The user's selected sources, enforced on every tool the agent can call.

    A checkbox in the sidebar is only a promise if the tool loop keeps it:
    the model may call search with its own filters, open any document by id,
    or run SQL against any table. Everything below intersects with, or
    refuses outside of, the selection so the answer can only be built from
    what the user picked. Tables are resolved lazily (one list_tables call)
    and only when a table tool is actually used.
    """

    def __init__(self, document_ids: List[str], collection_id: Optional[str], mcp):
        self.ids = set(document_ids)
        self.collection_id = collection_id
        self._mcp = mcp
        self._tables: Optional[List[Dict[str, Any]]] = None

    def merge_search_filters(self, filters: Any) -> Dict[str, Any]:
        merged = dict(filters) if isinstance(filters, dict) else {}
        requested = merged.get("document_ids")
        if isinstance(requested, list) and requested:
            allowed = [d for d in requested if d in self.ids]
            if not allowed:
                raise ValueError(
                    "Those documents are outside the sources the user selected for this "
                    "conversation. Search without document_ids to stay within the selection."
                )
            merged["document_ids"] = allowed
        else:
            merged["document_ids"] = sorted(self.ids)
        return merged

    def require_document(self, document_id: str) -> None:
        if document_id not in self.ids:
            raise ValueError(
                f"Document {document_id} is not among the sources the user selected for this "
                "conversation. Only the selected sources may be read."
            )

    def _load_tables(self) -> List[Dict[str, Any]]:
        if self._tables is None:
            try:
                data = self._mcp.list_tables(collection_id=self.collection_id)
                self._tables = list(data.get("tables") or [])
            except Exception:
                self._tables = []
        return self._tables

    def allowed_tables(self) -> List[Dict[str, Any]]:
        return [t for t in self._load_tables() if t.get("document_id") in self.ids]

    def require_table(self, identifier: str) -> None:
        for t in self._load_tables():
            if identifier in (t.get("table_name"), t.get("filename"), t.get("document_id")):
                if t.get("document_id") in self.ids:
                    return
                raise ValueError(
                    f"Table '{identifier}' belongs to a source outside the user's selection "
                    "for this conversation."
                )
        # Unknown identifier: let the tool report it in its own words.

    def require_sql(self, sql: str) -> None:
        allowed = {t.get("table_name") for t in self.allowed_tables() if t.get("table_name")}
        referenced = {
            t.get("table_name") for t in self._load_tables()
            if t.get("table_name") and re.search(rf'\b{re.escape(t["table_name"])}\b', sql)
        }
        outside = sorted(referenced - allowed)
        if outside:
            raise ValueError(
                f"SQL references table(s) outside the user's selected sources: {', '.join(outside)}. "
                f"Allowed tables: {', '.join(sorted(allowed)) or 'none'}."
            )


def execute_tool_calls(
    calls: List[Dict[str, Any]],
    agent_context: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    """Run each parsed tool call in-process via the MCP tool functions.

    `agent_context` carries the chat-level defaults the agent inherits when
    its tool call doesn't specify them — most importantly `collection_id`,
    and `document_ids` when the user limited the conversation to a
    selection of sources (enforced on every tool, see _SourceSelection).
    """
    # Lazy import — services.mcp_server pulls in heavy deps and the agent
    # loop is the only consumer here.
    from services import mcp_server as mcp

    ctx = agent_context or {}
    default_collection_id = ctx.get("collection_id")
    selection: Optional[_SourceSelection] = None
    if ctx.get("document_ids"):
        selection = _SourceSelection(list(ctx["document_ids"]), default_collection_id, mcp)

    results: List[Dict[str, Any]] = []
    for call in calls:
        if call.get("error"):
            results.append({
                "tool": call.get("tool"),
                "args": {"raw": call.get("raw")},
                "error": call["error"],
            })
            continue

        tool = call.get("tool")
        tool = _TOOL_ALIASES.get(tool, tool)
        if tool not in SUPPORTED_TOOLS:
            results.append({
                "tool": tool,
                "args": call,
                "error": f"Unknown tool '{tool}'. Supported: {sorted(SUPPORTED_TOOLS)}",
            })
            continue

        # A source selection pins the collection: the selected ids belong to
        # the current collection, so whatever the model passes here (usually
        # the collection's *name*, copied from the overview) is ignored rather
        # than refused — a refusal only makes the model retry until it gives up.
        # Without a selection an explicit id or name is honoured (the MCP layer
        # resolves names) so the user can ask about another collection.
        if selection is not None:
            collection_id = default_collection_id
        else:
            collection_id = call.get("collection_id") or default_collection_id
        args_for_log: Dict[str, Any] = {}

        try:
            if tool == "research_documents":
                filters = call.get("filters")
                if selection is not None:
                    filters = selection.merge_search_filters(filters)
                args_for_log = {
                    "query": call.get("query"), "collection_id": collection_id,
                    "subqueries": call.get("subqueries"), "top_k": call.get("top_k", 8),
                    "max_per_document": call.get("max_per_document", 2),
                    "max_context_chars": call.get("max_context_chars", 12000), "filters": filters,
                }
                data = mcp.research_documents_sync(**args_for_log)

            elif tool == "find_in_documents":
                filters = call.get("filters")
                if selection is not None:
                    filters = selection.merge_search_filters(filters)
                pattern = call.get("pattern")
                if not isinstance(pattern, str) or not pattern.strip() or len(pattern) > 1000:
                    raise ValueError("pattern must contain 1–1000 characters")
                args_for_log = {"pattern": pattern, "collection_id": collection_id,
                                "case_sensitive": call.get("case_sensitive", False),
                                "max_results": max(1, min(int(call.get("max_results", 20)), 20)),
                                "filters": filters}
                data = mcp.find_in_documents_sync(**args_for_log, literal=True)

            elif tool == "search_documents":
                query = call.get("query")
                if not query:
                    raise ValueError("search_documents requires 'query'")
                mode = call.get("mode")
                top_k = call.get("top_k")
                filters = call.get("filters")
                if selection is not None:
                    filters = selection.merge_search_filters(filters)
                args_for_log = {
                    "query": query, "mode": mode, "top_k": top_k,
                    "filters": filters, "collection_id": collection_id,
                }
                # Sync variant: this executor already runs off the event loop
                # (threadpool endpoint or asyncio.to_thread); the async MCP
                # tool wrapper would return a coroutine here.
                data = mcp.search_collection_sync(
                    query=query,
                    collection_id=collection_id,
                    mode=mode,
                    top_k=int(top_k) if top_k is not None else None,
                    filters=filters if isinstance(filters, dict) else None,
                )

            elif tool == "get_document_context":
                document_id = call.get("document_id")
                if not document_id:
                    raise ValueError("get_document_context requires 'document_id'")
                if selection is not None:
                    selection.require_document(document_id)
                args_for_log = {
                    "document_id": document_id,
                    "page_number": call.get("page_number"),
                    "chunk_id": call.get("chunk_id"),
                    "window": call.get("window", 1),
                    "collection_id": collection_id,
                }
                data = mcp.get_document_context(
                    document_id=document_id,
                    page_number=call.get("page_number"),
                    chunk_id=call.get("chunk_id"),
                    window=int(call.get("window", 1)),
                    max_chars=int(call.get("max_chars", 12000)),
                    collection_id=collection_id,
                )

            elif tool == "list_tables":
                args_for_log = {"collection_id": collection_id}
                data = mcp.list_tables(collection_id=collection_id)
                if selection is not None and isinstance(data, dict):
                    kept = [t for t in (data.get("tables") or []) if t.get("document_id") in selection.ids]
                    data = {**data, "tables": kept, "total_tables": len(kept),
                            "note": "Limited to the sources the user selected for this conversation."}

            elif tool == "get_table_schema":
                identifier = call.get("identifier") or call.get("table") or call.get("filename")
                if not identifier:
                    raise ValueError("get_table_schema requires 'identifier'")
                if selection is not None:
                    selection.require_table(identifier)
                args_for_log = {"identifier": identifier, "collection_id": collection_id}
                data = mcp.get_table_schema(identifier=identifier, collection_id=collection_id)

            elif tool == "get_table_rows":
                identifier = call.get("identifier") or call.get("table") or call.get("filename")
                if not identifier:
                    raise ValueError("get_table_rows requires 'identifier'")
                if selection is not None:
                    selection.require_table(identifier)
                limit = int(call.get("limit", 200))
                args_for_log = {"identifier": identifier, "limit": limit, "collection_id": collection_id}
                data = mcp.get_table_rows(
                    identifier=identifier,
                    limit=limit,
                    collection_id=collection_id,
                )

            elif tool == "query_table":
                sql = call.get("sql") or call.get("query")
                if not sql:
                    raise ValueError("query_table requires 'sql'")
                if selection is not None:
                    selection.require_sql(sql)
                max_rows = min(int(call.get("max_rows", _MAX_STRUCTURED_ROWS_PER_TOOL)), 2000)
                args_for_log = {"sql": sql, "max_rows": max_rows, "collection_id": collection_id}
                data = mcp.query_table(
                    sql=sql,
                    max_rows=max_rows,
                    collection_id=collection_id,
                )

            elif tool == "aggregate_table":
                identifier = call.get("identifier") or call.get("table") or call.get("filename")
                aggregate_col = call.get("aggregate_col") or call.get("column")
                agg_fn = call.get("agg_fn") or call.get("fn")
                if not identifier or not aggregate_col or not agg_fn:
                    raise ValueError("aggregate_table requires 'identifier', 'aggregate_col', 'agg_fn'")
                if selection is not None:
                    selection.require_table(identifier)
                args_for_log = {
                    "identifier": identifier,
                    "aggregate_col": aggregate_col,
                    "agg_fn": agg_fn,
                    "group_by": call.get("group_by"),
                    "sort_by": call.get("sort_by"),
                    "limit": call.get("limit"),
                    "collection_id": collection_id,
                }
                data = mcp.aggregate_table(
                    identifier=identifier,
                    aggregate_col=aggregate_col,
                    agg_fn=agg_fn,
                    group_by=call.get("group_by"),
                    sort_by=call.get("sort_by"),
                    limit=int(call["limit"]) if call.get("limit") is not None else None,
                    collection_id=collection_id,
                )

            elif tool == "list_collections":
                args_for_log = {}
                data = mcp.list_collections()

            elif tool == "get_collection_info":
                args_for_log = {"collection_id": collection_id}
                data = mcp.get_collection_info(collection_id=collection_id)

            elif tool == "get_collection_stats":
                args_for_log = {"collection_id": collection_id}
                data = mcp.collection_stats_payload(
                    collection_id, sorted(selection.ids) if selection is not None else None)

            elif tool == "list_documents":
                args_for_log = {k: call.get(k) for k in
                                ("limit", "offset", "name_contains", "file_type", "sort")}
                args_for_log["collection_id"] = collection_id
                data = mcp.list_documents_payload(
                    collection_id,
                    limit=max(1, min(int(call.get("limit") or 100), 500)),
                    offset=max(0, int(call.get("offset") or 0)),
                    name_contains=call.get("name_contains"),
                    file_type=call.get("file_type"),
                    sort=call.get("sort") or "newest",
                    document_ids=sorted(selection.ids) if selection is not None else None,
                )

            else:
                raise ValueError(f"No dispatcher for tool '{tool}'")

            logger.info("[agent] tool=%s args=%s -> ok", tool, _summarize_args(args_for_log))
            results.append({"tool": tool, "args": args_for_log, "result": data})

        except SQLValidationError as e:
            logger.info("[agent] tool=%s args=%s -> sql_rejected: %s", tool, _summarize_args(args_for_log), e)
            results.append({"tool": tool, "args": args_for_log or call, "error": f"SQL rejected: {e}"})
        except ValueError as e:
            logger.info("[agent] tool=%s args=%s -> error: %s", tool, _summarize_args(args_for_log), e)
            results.append({"tool": tool, "args": args_for_log or call, "error": str(e)})
        except Exception as e:
            logger.exception("[agent] tool=%s args=%s -> exception", tool, _summarize_args(args_for_log))
            results.append({"tool": tool, "args": args_for_log or call, "error": f"Tool execution failed: {e}"})
    return results


def _summarize_args(args: Dict[str, Any]) -> str:
    """Compact one-line representation of tool args for logs."""
    if not args:
        return "{}"
    parts = []
    for k, v in args.items():
        if v is None:
            continue
        if isinstance(v, str) and len(v) > 80:
            v = v[:77] + "..."
        parts.append(f"{k}={v!r}")
    return "{" + ", ".join(parts) + "}"


def format_results_for_prompt(results: List[Dict[str, Any]]) -> str:
    """Render tool results as JSON blocks for the next-pass prompt."""
    if not results:
        return ""
    lines = ["TOOL RESULTS:"]
    for r in results:
        payload = {k: v for k, v in r.items() if k in ("tool", "args", "result", "error")}
        # Truncate very large query results to keep the prompt bounded.
        result_obj = payload.get("result")
        if isinstance(result_obj, dict) and "rows" in result_obj:
            rows = result_obj.get("rows") or []
            if len(rows) > 50:
                payload["result"] = {**result_obj, "rows": rows[:50],
                                      "display_note": f"truncated to 50 of {len(rows)} rows for prompt"}
        lines.append("<tool_result>")
        lines.append(json.dumps(payload, default=str))
        lines.append("</tool_result>")
    return "\n".join(lines)


def strip_tool_calls(text: str) -> str:
    """Remove `<tool_call>` blocks from the final assistant text so users don't see them."""
    return _TOOL_CALL_RE.sub('', text).strip()


_DEFAULT_INLINE_ROW_LIMIT = 150
_DEFAULT_INLINE_CHAR_BUDGET = 60000
_CELL_CHAR_LIMIT = 240


def render_table_as_jsonl(
    store: StructuredStore,
    table_info: Dict[str, Any],
    max_rows: int = _DEFAULT_INLINE_ROW_LIMIT,
) -> Optional[str]:
    """Dump a full structured table as JSONL (one row per line) using original column names.

    Returns None if the table is empty or the query fails. Long string values are
    truncated to keep prompt size bounded.
    """
    rendered = render_table_as_rows(store, table_info, max_rows=max_rows)
    if not rendered:
        return None
    columns = rendered["columns"]
    rows = rendered["rows"]
    lines: List[str] = []
    for row in rows:
        obj: Dict[str, Any] = {display_name: row[i] for i, display_name in enumerate(columns)}
        lines.append(json.dumps(obj, default=str, ensure_ascii=False))
    return "\n".join(lines)


def render_table_as_rows(
    store: StructuredStore,
    table_info: Dict[str, Any],
    max_rows: int = _DEFAULT_INLINE_ROW_LIMIT,
) -> Optional[Dict[str, Any]]:
    """Return a full structured table as {"columns": [...], "rows": [[...], ...]}.

    Uses original (display) column names. Long string values are truncated
    to keep response size bounded. Returns None if the table is empty or the
    query fails.
    """
    table_name = table_info.get("table_name")
    if not table_name:
        return None
    if (table_info.get("row_count") or 0) == 0:
        return None

    sql = f'SELECT * FROM "{table_name}" ORDER BY __row_number'
    try:
        data = store.execute_query(sql, max_rows=max_rows)
    except Exception as e:
        logger.warning(f"render_table_as_rows failed for {table_name}: {e}")
        return None

    raw_columns: List[str] = data.get("columns") or []
    raw_rows: List[List[Any]] = data.get("rows") or []
    if not raw_columns or not raw_rows:
        return None

    sql_to_orig = {
        c["sql_name"]: c["name"]
        for c in table_info.get("columns", [])
        if c.get("sql_name") and c.get("name")
    }
    keep_idx: List[int] = [
        i for i, col in enumerate(raw_columns) if col != "__row_number"
    ]
    columns: List[str] = [
        sql_to_orig.get(raw_columns[i], raw_columns[i]) for i in keep_idx
    ]

    rows: List[List[Any]] = []
    for raw_row in raw_rows:
        projected: List[Any] = []
        for i in keep_idx:
            val = raw_row[i]
            if isinstance(val, str) and len(val) > _CELL_CHAR_LIMIT:
                val = val[:_CELL_CHAR_LIMIT] + "..."
            projected.append(val)
        rows.append(projected)

    return {"columns": columns, "rows": rows}


# Only tables from genuinely tabular source files are inline candidates.
# Tables *extracted* from PDFs/DOCX also land in the structured store, and a
# document-heavy collection can hold dozens of small ones — inlining those
# bloats every search/chat prompt with marginal tables AND (via
# inlined_filenames → skip_filenames) suppresses the document's text chunks
# from retrieval. They remain fully queryable through the SQL tool loop.
_TABULAR_SOURCE_EXTS = {".csv", ".tsv", ".xlsx", ".xls"}


def _is_tabular_source(filename: str | None) -> bool:
    if not filename:
        return False
    dot = filename.rfind(".")
    return dot != -1 and filename[dot:].lower() in _TABULAR_SOURCE_EXTS


def build_structured_context(
    tables: List[Dict[str, Any]],
    stores: Dict[str, StructuredStore],
    inline_row_threshold: int = _DEFAULT_INLINE_ROW_LIMIT,
    total_char_budget: int = _DEFAULT_INLINE_CHAR_BUDGET,
) -> Dict[str, Any]:
    """Split structured tables into (small → inline JSONL, large → tool loop).

    For small CSV/XLSX tables we render the FULL contents into the prompt so
    the model can answer numeric/aggregation questions without depending on
    top-K chunk retrieval. Large tables — and tables extracted from
    non-tabular documents like PDFs — fall back to the SQL tool-use loop.

    Returns a dict:
      - inline_block: str, full JSONL prompt section (or '')
      - tool_tables: list[dict], the subset still needing the tool loop
      - inlined_filenames: set[str], filenames fully represented in the prompt
      - inlined_document_ids: set[str], document_ids fully represented
    """
    inline_parts: List[str] = []
    tool_tables: List[Dict[str, Any]] = []
    inlined_filenames: Set[str] = set()
    inlined_document_ids: Set[str] = set()
    running = 0

    for t in tables:
        row_count = t.get("row_count") or 0
        cid = t.get("collection_id")
        store = stores.get(cid) if cid else None

        if (
            not store
            or row_count == 0
            or row_count > inline_row_threshold
            or not _is_tabular_source(t.get("filename"))
        ):
            tool_tables.append(t)
            continue

        jsonl = render_table_as_jsonl(store, t, max_rows=inline_row_threshold)
        if jsonl is None:
            tool_tables.append(t)
            continue

        sheet_suffix = f" [sheet: {t['sheet_name']}]" if t.get("sheet_name") else ""
        role_cols = [
            f'{c["name"]}→{c["role"]}'
            for c in t.get("columns", [])
            if c.get("role")
        ]
        roles_line = f"  detected roles: {', '.join(role_cols)}\n" if role_cols else ""
        sql_line = (
            f'  sql_table_name: {t["table_name"]}  '
            f'(use this exact string if you need to run SQL against this file)\n'
            if t.get("table_name")
            else ""
        )
        header = (
            f'--- CSV FILE: {t["filename"]}{sheet_suffix} '
            f'({row_count} rows, {t.get("column_count", 0)} cols) ---\n'
            f'{sql_line}'
            f'{roles_line}'
        )
        block = header + jsonl

        if running + len(block) > total_char_budget:
            tool_tables.append(t)
            continue

        inline_parts.append(block)
        running += len(block)
        if t.get("filename"):
            inlined_filenames.add(t["filename"])
        if t.get("document_id"):
            inlined_document_ids.add(t["document_id"])

    inline_block = ""
    if inline_parts:
        inline_block = (
            "STRUCTURED TABLES (AUTHORITATIVE FULL DATA):\n"
            "The complete contents of the following CSV/XLSX files are included "
            "below as JSONL — one JSON object per row, every row present. For "
            "any numeric, aggregation, sum, count, average, filter, ranking, or "
            "date-range question about these files, answer DIRECTLY and "
            "EXCLUSIVELY from the JSONL rows below. Do NOT rely on any chunk "
            "snippets for these files — the JSONL is complete and authoritative. "
            "Show your arithmetic when summing so the user can verify.\n\n"
            + "\n\n".join(inline_parts)
        )

    return {
        "inline_block": inline_block,
        "tool_tables": tool_tables,
        "inlined_filenames": inlined_filenames,
        "inlined_document_ids": inlined_document_ids,
    }


def collect_structured_tables(
    collection_ids: List[str],
    document_ids: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, StructuredStore]]:
    """Gather structured tables from every collection in scope.

    ``document_ids`` limits the result to tables that came from those
    sources, so a conversation scoped to a selection never inlines or
    advertises a spreadsheet the user did not pick.

    Returns (tables, stores_by_collection).
    """
    from services.indexer_manager import indexer_manager

    selected = set(document_ids) if document_ids else None
    all_tables: List[Dict[str, Any]] = []
    stores: Dict[str, StructuredStore] = {}
    for cid in collection_ids:
        try:
            indexer = indexer_manager.get_indexer(cid)
        except Exception as e:
            logger.warning(f"collect_structured_tables: cannot load indexer '{cid}': {e}")
            continue
        store = indexer.vector_store.structured_store
        stores[cid] = store
        try:
            tables = store.list_tables()
        except Exception as e:
            logger.warning(f"collect_structured_tables: list_tables failed for '{cid}': {e}")
            continue
        for t in tables:
            if selected is not None and t.get("document_id") not in selected:
                continue
            t_copy = dict(t)
            t_copy["collection_id"] = cid
            all_tables.append(t_copy)
    return all_tables, stores
