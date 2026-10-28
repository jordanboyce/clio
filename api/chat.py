"""Chat and AI-provider endpoints."""

import asyncio
import json
import logging
import re
import time

from fastapi import Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from middleware.user_context import get_request_user
from services.usage_service import check_daily_budget, record_chat_usage

from services.ai_service import AIService, AnthropicProvider, create_provider
from services import authored_text
from services.agent_tools import anthropic_tools, openai_tools
from services.collection_overview import build_collection_overview as _build_collection_overview
from services.collection_service import collection_service
from services.chat_evidence import ChatEvidence, CITATION_INSTRUCTIONS
from services.chat_followups import (
    QUICK_ANSWER_INSTRUCTIONS,
    related_questions as _related_questions,
    starter_questions as _starter_questions,
)
from services.research_search import RESEARCH_INSTRUCTIONS
from services.structured_chat import (
    build_structured_context,
    build_tool_use_instructions,
    collect_structured_tables,
    describe_tables_for_prompt,
    execute_tool_calls,
    format_results_for_prompt,
    parse_tool_calls,
    strip_tool_calls,
)
from models.schemas import (
    AIUsage,
    AIUsageDetail,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatSource,
    StarterQuestionsRequest,
    StarterQuestionsResponse,
)

from fastapi import APIRouter
from api.deps import (
    ALL_AI_PROVIDERS,
    CLOUD_AI_PROVIDERS,
    _truncate_tool_result,
    expertise_store,
    get_indexer,
    resolve_ai_key,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Semantic answer cache ───────────────────────────────────────────────────
# Repeated single-turn questions skip the provider entirely when a previous
# answer's question is close enough (local-embedding cosine similarity) and
# every source document that answer cited is unchanged. See
# services/answer_cache.py for the rules; these helpers own the pieces that
# need chat-layer context (indexers, document metadata, source URLs).

_FINGERPRINT_FIELDS = ("filename", "upload_timestamp", "num_chunks", "content_hash", "sensitivity")


def _selected_document_ids(chat_request: ChatRequest) -> list[str] | None:
    """The user's source selection, normalized; only meaningful for the
    current-collection scope (ids belong to one collection)."""
    ids = getattr(chat_request, "document_ids", None)
    if not ids or chat_request.scope == "all":
        return None
    cleaned = sorted({str(d).strip() for d in ids if str(d).strip()})
    return cleaned or None


def _requested_model(chat_request: ChatRequest, x_ai_model=None, x_ollama_model=None,
                     x_anthropic_model=None, x_openai_model=None, x_ai_base_url=None,
                     x_ai_key=None) -> str:
    """The model the request will actually run on, as the provider block
    resolves it — folded into the cache key so a switch of model or
    endpoint never serves the other one's answer."""
    if chat_request.provider == "ollama":
        model = x_ai_model or x_ollama_model or "llama3.2"
    else:
        model = x_ai_model or x_anthropic_model or x_openai_model or ""
    return f"{model}@{x_ai_base_url or ''}"


def _collection_guidance_version(collection_id: str) -> str:
    """Guide text + attached expertise, as a short digest.

    Both shape the system prompt, so a cached answer written under different
    guidance must not be reused after either changes."""
    import hashlib

    parts = []
    try:
        col = collection_service.get_collection(collection_id) or {}
        parts.append(col.get("guide") or "")
        parts.append(col.get("description") or "")
    except Exception:
        parts.append("")
    try:
        packs = expertise_store.get_packs_for_collection(collection_id)
        parts.extend(f"{p.id}:{p.updated_at}" for p in packs)
    except Exception:
        pass
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]


def _corpus_version(collection_id: str) -> str:
    try:
        return get_indexer(collection_id).vector_store.metadata_store.corpus_version()
    except Exception:
        return "?"


def _request_fingerprint(chat_request: ChatRequest, collection_ids: list[str], model: str) -> str:
    """Everything besides the question that decides the answer.

    Provider + model + endpoint, retrieval settings, the source selection,
    and per collection the corpus version and guidance digest. Hashed into
    the scope key so a cached answer is only ever reused for a request that
    would have been built the same way — new uploads, a guide edit, an
    attached instruction pack, a model switch or a different selection all
    miss the cache instead of returning a stale answer.
    """
    import hashlib

    parts = [
        "evidence_protocol=3",
        f"provider={chat_request.provider}",
        f"model={model}",
        f"mode={getattr(chat_request.mode, 'value', chat_request.mode)}",
        f"top_k={chat_request.top_k}",
        f"rerank={int(bool(chat_request.rerank))}",
        f"depth={getattr(chat_request, 'depth', 'research')}",
        "selection=" + ",".join(_selected_document_ids(chat_request) or []),
    ]
    for cid in collection_ids:
        parts.append(f"{cid}:corpus={_corpus_version(cid)}:guidance={_collection_guidance_version(cid)}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _cache_context(chat_request: ChatRequest, collection_id: str, model: str = ""):
    """Scope key + query embedding for this request, or None when the cache
    doesn't apply (disabled, or a multi-turn conversation — follow-ups depend
    on history a cached answer never saw).

    The scope key is ``<scope>#<request fingerprint>``: the scope names the
    collection(s), the fingerprint pins everything else that shapes the
    answer (see _request_fingerprint). Clearing a scope drops every
    fingerprint under it."""
    from config import settings

    if not settings.enable_answer_cache:
        return None
    if chat_request.messages[:-1]:
        return None

    try:
        if chat_request.scope == "all":
            # Visible collections define the scope: in private-collections
            # mode two users with different visibility never share entries.
            ids = sorted(c["id"] for c in collection_service.get_all_collections())
            if not ids:
                return None
            scope = "all:" + ",".join(ids)
            embed_cid = "default" if "default" in ids else ids[0]
        else:
            ids = [collection_id]
            scope = f"col:{collection_id}"
            embed_cid = collection_id

        scope_key = f"{scope}#{_request_fingerprint(chat_request, ids, model)}"
        embedder = get_indexer(embed_cid).embedding_service
        question = chat_request.messages[-1].content.strip()
        return {
            "scope_key": scope_key,
            "embedding_model": embedder.model_name,
            "vec": embedder.embed_query(question),
            "question": question,
        }
    except Exception as e:
        logger.debug(f"Answer cache unavailable for this request: {e}")
        return None


def _document_fingerprint(collection_id: str, document_id: str) -> dict | None:
    from services.governance import effective_sensitivity, is_hidden

    try:
        # Reauthorize on every cache hit: a share may have been revoked since
        # the question's scope was resolved. Never bypass the API access gate.
        info = get_indexer(collection_id).vector_store.metadata_store.get_document_info(document_id)
        if not info or is_hidden(info):
            return None
        collection = collection_service.get_collection(collection_id)
        fingerprint = {field: info.get(field) for field in _FINGERPRINT_FIELDS}
        fingerprint["sensitivity"] = effective_sensitivity(collection, info.get("sensitivity"))
        return fingerprint
    except Exception:
        return None


# Tokens that flip the meaning of a question without moving its embedding
# much: numbers/identifiers ("2024" vs "2025", "Q3", "v1.2") and negations
# ("is permitted" vs "is not permitted"). Two questions may only share a
# cached answer when these agree.
_LOAD_BEARING_RE = re.compile(
    r"[a-z]+n't\b"
    r"|\w*\d[\w./%-]*"
    r"|\b(?:not|no|never|none|nor|without|except|unless|cannot|neither)\b"
)


def _normalized_question(text: str) -> str:
    return " ".join(text.split()).casefold()


def _load_bearing_tokens(text: str) -> list[str]:
    return sorted(m.rstrip(".,") for m in _LOAD_BEARING_RE.findall(text.casefold()))


def _same_question(stored: str, asked: str, threshold: float) -> bool:
    """Whether a cached question may stand in for the one being asked.

    The embedding similarity (already at or above ``threshold``) is only a
    candidate lookup. At threshold 1.0 only the identical text counts
    (whitespace and case aside). Below that the rephrasing is accepted, but
    numbers and negations must agree: embeddings place "2024 revenue" next
    to "2025 revenue", and those are different questions."""
    if _normalized_question(stored) == _normalized_question(asked):
        return True
    if threshold >= 1.0:
        return False
    return _load_bearing_tokens(stored) == _load_bearing_tokens(asked)


def _cache_lookup(cache_ctx, threshold: float | None = None):
    """Best fresh cached entry for this question, or None. Entries whose
    source documents changed or vanished are discarded on sight.

    ``threshold`` is the request's own similarity floor (Chat Settings
    slider); None means the deployment default."""
    from config import settings
    from services.answer_cache import answer_cache

    if threshold is None:
        threshold = settings.answer_cache_threshold
    entry = answer_cache.find_best(
        cache_ctx["scope_key"], cache_ctx["embedding_model"],
        cache_ctx["vec"], threshold,
    )
    if entry is None:
        return None
    if not _same_question(entry["question"], cache_ctx["question"], threshold):
        return None
    # Legacy or incomplete entries cannot establish the provenance of every
    # returned source, so regenerate rather than serving unvalidated text.
    source_ids = {(s.get("collection_id"), s.get("document_id")) for s in entry["sources"]}
    fingerprint_ids = {(fp.get("collection_id"), fp.get("document_id")) for fp in entry["fingerprints"]}
    if not source_ids or source_ids != fingerprint_ids:
        answer_cache.delete(entry["id"])
        return None
    for fp in entry["fingerprints"]:
        current = _document_fingerprint(fp["collection_id"], fp["document_id"])
        if current != {field: fp.get(field) for field in _FINGERPRINT_FIELDS}:
            answer_cache.delete(entry["id"])
            logger.info("Answer cache: stale or inaccessible source; entry discarded")
            return None
    answer_cache.mark_hit(entry["id"])
    logger.debug("Answer cache hit (similarity %s)", entry["similarity"])
    return entry


# ── Answer depth and follow-ups ─────────────────────────────────────────────
# depth='quick' answers in one pass from the retrieved context: no document
# research tools, so time-to-answer is one provider round-trip. Large tables
# are the exception — they only exist behind the table tools, so those stay
# callable. depth='research' is the full agent loop.

_TABLE_TOOL_NAMES = frozenset({
    "list_tables", "get_table_schema", "get_table_rows", "query_table", "aggregate_table",
})


def _is_quick(chat_request: ChatRequest) -> bool:
    return getattr(chat_request, "depth", "research") == "quick"


def _tools_for_depth(chat_request: ChatRequest, tools_spec: list, tool_tables) -> list:
    """The tool list a turn may use: everything for research, only the table
    tools (and only when large tables exist) for a quick answer."""
    if not _is_quick(chat_request):
        return tools_spec
    if not tool_tables:
        return []
    return [t for t in tools_spec
            if (t.get("name") or (t.get("function") or {}).get("name")) in _TABLE_TOOL_NAMES]


def _depth_instructions(chat_request: ChatRequest) -> str:
    return QUICK_ANSWER_INSTRUCTIONS if _is_quick(chat_request) else RESEARCH_INSTRUCTIONS


def _followups(chat_request: ChatRequest, provider, ai_service, question: str,
               answer: str, filtered_results) -> tuple[list[str], dict]:
    """Suggested follow-up questions for a finished turn, with the usage of
    the extra call. Best effort: never turns a good answer into a failed turn."""
    if not getattr(chat_request, "related", True) or not (answer or "").strip():
        return [], {}
    try:
        out = _related_questions(
            provider, ai_service.fast_model, question, answer,
            [r.filename for r, _ in filtered_results],
        )
        return out.get("questions") or [], out.get("usage") or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Follow-up questions skipped: %s", exc)
        return [], {}


def _cache_store(cache_ctx, answer_text: str, filtered_results, provider: str,
                 related: list[str] | None = None):
    """Record a freshly generated answer. Answers without sources are never
    cached — 'nothing found' should stay retryable, and there would be no
    fingerprints to invalidate on."""
    from services.answer_cache import answer_cache

    if not answer_text or not filtered_results:
        return
    sources, fingerprints, seen = [], [], set()
    label_cache: dict = {}
    for r, cid in filtered_results:
        sources.append({
            "filename": r.filename,
            "page_number": r.page_number,
            "text_snippet": r.text_snippet,
            "similarity_score": r.similarity_score,
            "document_id": r.document_id,
            "collection_id": cid,
            "sensitivity": _source_sensitivity(cid, r, label_cache),
        })
        if (cid, r.document_id) not in seen:
            seen.add((cid, r.document_id))
            fp = _document_fingerprint(cid, r.document_id)
            if fp is None:
                return  # Do not persist an answer whose sources cannot be verified.
            fingerprints.append({"collection_id": cid, "document_id": r.document_id, **fp})
    try:
        answer_cache.store(
            cache_ctx["scope_key"], cache_ctx["embedding_model"],
            cache_ctx["question"], cache_ctx["vec"],
            answer_text, sources, fingerprints, provider,
            related=related,
        )
    except Exception as e:
        logger.warning(f"Answer cache store failed: {e}")


def _cached_sources(entry, base_url: str) -> list[dict]:
    """Rebuild source payloads with URLs for the current host."""
    return [
        {
            "filename": s["filename"],
            "page_number": s["page_number"],
            "text_snippet": s["text_snippet"],
            "similarity_score": s["similarity_score"],
            "document_id": s["document_id"],
            "pdf_url": f"{base_url}/documents/{s['document_id']}/pdf?collection_id={s['collection_id']}",
            "page_url": f"{base_url}/documents/{s['document_id']}/pdf?collection_id={s['collection_id']}#page={s['page_number']}",
            "sensitivity": s.get("sensitivity"),
        }
        for s in entry["sources"]
    ]


def _source_sensitivity(collection_id: str, result, _cache: dict) -> str:
    """Label in force for a retrieved chunk: document override, else the
    collection's. Collections are looked up once per turn."""
    from services.governance import effective_sensitivity

    if collection_id not in _cache:
        _cache[collection_id] = collection_service.get_collection(collection_id)
    return effective_sensitivity(_cache[collection_id], getattr(result, "sensitivity", None))


def _rerank_context(ai_service, query, results, collection_ids, top_k):
    """Keep passages and provenance paired for both chat transports."""
    if not results:
        return results, collection_ids, None
    try:
        ranked = ai_service.rerank_results(query, [
            {"index": i, "filename": r.filename, "text_snippet": r.text_snippet,
             "similarity_score": r.similarity_score}
            for i, r in enumerate(results)
        ], top_k)
        indices = list(dict.fromkeys(
            i for i in ranked["reranked_indices"]
            if type(i) is int and 0 <= i < len(results)
        ))
        if indices:
            return [results[i] for i in indices], [collection_ids[i] for i in indices], ranked.get("usage")
        return results, collection_ids, ranked.get("usage")
    except Exception as exc:
        logger.warning("Chat context reranking failed, using original order: %s", exc)
        return results, collection_ids, None


@router.get("/api/chat/cache", tags=["chat"], summary="Answer cache status")
async def answer_cache_status():
    """Whether the semantic answer cache is on and the deployment's default
    similarity threshold — what a request gets when it omits
    ``cache_threshold``. The Chat Settings slider seeds itself from this."""
    from config import settings

    return {
        "enabled": bool(settings.enable_answer_cache),
        "threshold": float(settings.answer_cache_threshold),
    }


@router.delete("/api/chat/cache", tags=["chat"], summary="Clear the semantic answer cache")
async def clear_answer_cache(collection_id: str = None):
    """Drop cached answers — for one collection, or all of them."""
    from services.answer_cache import answer_cache
    from api.deps import require_admin, require_collection_access

    if collection_id:
        require_collection_access(collection_id, get_request_user(), write=True)
    else:
        require_admin("clear all cached answers")

    removed = answer_cache.clear(f"col:{collection_id}" if collection_id else None)
    return {"cleared": removed}


# ---------------------------------------------------------------------------
# One chat turn
# ---------------------------------------------------------------------------
# Both chat routes run the same pipeline — cache → provider → retrieval →
# prompt → agent loop → follow-ups → usage. `_run_turn` is that pipeline as
# a synchronous generator of events: /api/chat drains it into a ChatResponse,
# /api/chat/stream relays each event as it happens.
#
# Event types:
#   error       — {"message", "_status"}; always the last event
#   thinking    — prose the model emitted alongside tool calls
#   tool_start  — a tool call is about to execute
#   tool_end    — tool call finished (result included)
#   text        — the final answer (the SSE route streams it as text_delta)
#   sources     — the source list
#   related     — suggested follow-up questions (only when there are some)
#   done        — completion marker: usage, structured_results, sources, …
# Keys starting with "_" are for the in-process caller and never leave the
# server.

_NO_CONTEXT = "No relevant context found."
_EMPTY_STRUCTURED: dict = {
    "inline_block": "", "tool_tables": [], "inlined_filenames": set(), "inlined_document_ids": set(),
}
_FINAL_ANSWER_FALLBACK = (
    "I ran several tool calls but couldn't settle on a final answer — "
    "please rephrase or narrow the question."
)


def _prepare_turn(chat_request: ChatRequest, collection_id: str, model_hint: str):
    """Answer-cache lookup, done before any provider is built: a hit needs
    no provider (or API key) and, because it spends nothing, stays
    available to a user who is over their daily budget."""
    if not any(m.role == "user" for m in chat_request.messages):
        return None, None
    try:
        cache_ctx = _cache_context(chat_request, collection_id, model_hint)
        if cache_ctx and chat_request.use_cache:
            return cache_ctx, _cache_lookup(cache_ctx, chat_request.cache_threshold)
        return cache_ctx, None
    except Exception as e:
        logger.warning(f"Answer cache lookup failed: {e}")
        return None, None


def _supersede_fields(r) -> dict:
    """Source-payload fields for a passage from a replaced document and for
    folded copies; absent when the passage is ordinary."""
    out = {}
    if getattr(r, "superseded_by", None):
        out["superseded_by"] = r.superseded_by
    if getattr(r, "duplicates", None):
        out["duplicates"] = r.duplicates
        out["duplicate_count"] = getattr(r, "duplicate_count", None) or len(r.duplicates)
    return out


def _supersede_note(r) -> str:
    from services.supersede import source_note

    return source_note(r)


def _source_dicts(results, base_url: str) -> list[dict]:
    label_cache: dict = {}
    return [
        {
            "filename": r.filename,
            "page_number": r.page_number,
            "text_snippet": r.text_snippet,
            "similarity_score": r.similarity_score,
            "document_id": r.document_id,
            "pdf_url": f"{base_url}/documents/{r.document_id}/pdf?collection_id={cid}",
            "page_url": f"{base_url}/documents/{r.document_id}/pdf?collection_id={cid}#page={r.page_number}",
            "sensitivity": _source_sensitivity(cid, r, label_cache),
            **_supersede_fields(r),
        }
        for r, cid in results
    ]


def _expertise_block(collection_id: str) -> str:
    """Guidance from the Expertise packs attached to a collection, or ''.

    Each pack is its own delimited, labelled block (see services/authored_text):
    the text was written by a person, so it is placed in the prompt as context
    that cannot close its own block or speak with the system's voice. A pack the
    save-time scan flagged is still delivered, marked as flagged."""
    try:
        packs = expertise_store.get_packs_for_collection(collection_id)
    except Exception as e:
        logger.warning("Failed to load expertise packs for collection %s: %s", collection_id, e)
        return ""
    if not packs:
        return ""
    return "EXPERTISE (analysis frameworks attached to this collection):\n" + "\n\n".join(
        authored_text.wrap_block(
            "expertise", p.name,
            f"{(p.description + chr(10)) if p.description else ''}{p.body}",
            flag=p.flag, author=authored_text.AUTHOR_ADMIN,
        )
        for p in packs
    )


def _tool_result_messages(tool_calls: list, results: list, is_anthropic: bool) -> list[dict]:
    """Tool results in the provider's native message shape."""
    payloads = [
        _truncate_tool_result(json.dumps(res.get("error") or res.get("result") or {}, default=str))
        for res in results
    ]
    if is_anthropic:
        return [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tc["id"], "content": payload,
             **({"is_error": True} if res.get("error") else {})}
            for tc, res, payload in zip(tool_calls, results, payloads)
        ]}]
    return [{"role": "tool", "tool_call_id": tc["id"], "content": payload}
            for tc, payload in zip(tool_calls, payloads)]


def _run_turn(chat_request: ChatRequest, collection_id: str, base_url: str,
              provider_args: dict, cache_ctx, cache_entry):  # noqa: C901
    turn_started = time.time()
    turn_user = get_request_user()
    usage_collection = "all" if chat_request.scope == "all" else collection_id

    def _done(usage: dict, structured_results: list, sources: list, related: list, **extra) -> dict:
        record_chat_usage(
            turn_user, usage_collection, chat_request.provider, usage["model"],
            input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
            tool_calls=sum(1 for r in structured_results if r.get("tool") != "_thinking"),
            duration_ms=int((time.time() - turn_started) * 1000),
            **({"cache_hit": True} if extra.get("cached") else {}),
        )
        return {"type": "done", "usage": usage, "structured_results": structured_results,
                "sources": sources, "related_questions": related, "depth": chat_request.depth,
                "cached": False, **extra}

    # ---------- cached answer: no provider, no tokens ----------------------
    if cache_entry:
        sources = _cached_sources(cache_entry, base_url)
        related = cache_entry.get("related") or []
        yield {"type": "text", "text": cache_entry["answer"], "_whole": True}
        yield {"type": "sources", "sources": sources}
        if related:
            yield {"type": "related", "questions": related}
        yield _done({"input_tokens": 0, "output_tokens": 0, "model": "answer-cache"}, [], sources,
                    related, cached=True, cached_question=cache_entry["question"],
                    cached_similarity=(round(float(cache_entry["similarity"]), 4)
                                       if cache_entry.get("similarity") is not None else None),
                    _answer=cache_entry["answer"])
        return

    user_messages = [m for m in chat_request.messages if m.role == "user"]
    if not user_messages:
        yield {"type": "error", "message": "No user messages in conversation", "_status": 400}
        return
    latest_query = user_messages[-1].content

    try:
        provider = _build_provider(chat_request.provider, **provider_args)
    except HTTPException as e:
        yield {"type": "error", "message": e.detail, "_status": e.status_code}
        return
    ai_service = AIService(provider=provider)

    try:
        # ---------- retrieval ----------------------------------------------
        # Follow-up questions ("what date was that?") reference prior turns the
        # index knows nothing about; rewriting them into self-contained queries
        # is what makes multi-turn retrieval work. Single-turn queries skip the
        # extra provider call.
        prior = [{"role": m.role, "content": m.content} for m in chat_request.messages[:-1]]
        search_query = ai_service.reformulate_query(prior, latest_query) if prior else latest_query

        # The user's source selection constrains everything downstream:
        # retrieval, the inlined tables, the overview and every tool call.
        selected_ids = _selected_document_ids(chat_request)
        hits: list[tuple] = []
        if chat_request.scope == "all":
            overview_ids = [c["id"] for c in collection_service.get_all_collections()]
            for cid in overview_ids:
                try:
                    found = get_indexer(cid).search(
                        query=search_query, top_k=chat_request.top_k, mode=chat_request.mode,
                    )
                    hits.extend((r, cid) for r in found["results"])
                except Exception as e:
                    logger.warning(f"Chat: search failed for collection '{cid}': {e}")
            hits.sort(key=lambda h: h[0].similarity_score, reverse=True)
            hits = hits[:chat_request.top_k]
        else:
            overview_ids = [collection_id]
            try:
                indexer = get_indexer(collection_id)
            except ValueError as e:
                yield {"type": "error", "message": str(e), "_status": 404}
                return
            found = indexer.search(
                query=search_query, top_k=chat_request.top_k, mode=chat_request.mode,
                filters={"document_ids": selected_ids} if selected_ids else None,
            )
            hits = [(r, collection_id) for r in found["results"]]

        context_results = [r for r, _ in hits]
        result_collection_ids = [cid for _, cid in hits]
        rerank_usage = None
        if chat_request.rerank and context_results:
            context_results, result_collection_ids, rerank_usage = _rerank_context(
                ai_service, latest_query, context_results, result_collection_ids, chat_request.top_k,
            )
        rerank_usage = rerank_usage or {}

        # ---------- structured tables --------------------------------------
        # Small CSV/XLSX tables are inlined whole as JSONL (and their chunks
        # dropped: the JSONL is authoritative); large ones stay behind tools.
        structured_tables, structured_stores = collect_structured_tables(overview_ids, selected_ids)
        structured_ctx = (build_structured_context(structured_tables, structured_stores)
                          if structured_tables else _EMPTY_STRUCTURED)
        inline_block = structured_ctx["inline_block"]
        tool_tables = structured_ctx["tool_tables"]
        tables_block = describe_tables_for_prompt(tool_tables) if tool_tables else ""

        filtered = [(r, cid) for r, cid in zip(context_results, result_collection_ids)
                    if r.filename not in structured_ctx["inlined_filenames"]]
        context_text = "\n\n---\n\n".join(
            f"[Source {i + 1}: {r.filename}, page {r.page_number}{_supersede_note(r)}]\n{r.text_snippet}"
            for i, (r, _) in enumerate(filtered)
        ) or _NO_CONTEXT
        # Evidence keeps citation numbering stable as tool calls discover more
        # passages; its `results` list is the source list for the whole turn.
        evidence = ChatEvidence(filtered)

        try:
            collection_overview = _build_collection_overview(overview_ids, selected_ids)
        except Exception as e:
            logger.warning(f"Failed to build collection overview: {e}")
            collection_overview = "(Collection overview unavailable.)"

        # ---------- system prompt ------------------------------------------
        if selected_ids:
            scope_note = f"the {len(selected_ids)} source(s) the user selected in the current collection"
        else:
            scope_note = "all collections" if chat_request.scope == "all" else "the current collection"
        system_parts = [
            _depth_instructions(chat_request),
            CITATION_INSTRUCTIONS,
            f"You are an analytical research assistant working over the user's indexed sources in {scope_note}.",
            "Ground every claim in the provided sources, cite them, and say so plainly when the "
            "sources don't contain the answer rather than guessing.",
            "Answer the user's question using the COLLECTION OVERVIEW, STRUCTURED TABLES (when "
            "provided), and RETRIEVED CONTEXT below.",
            "Use the COLLECTION OVERVIEW for meta-questions about the knowledge base itself "
            "(file counts, available documents, date ranges).",
        ]
        if inline_block:
            system_parts.append(
                "When STRUCTURED TABLES are included below, they are the FULL contents of CSV/XLSX "
                "files as JSONL — every row is present. For any numeric, sum, count, average, filter, "
                "date-range, or ranking question about those files, answer DIRECTLY from the JSONL "
                "rows and show your arithmetic. Do NOT guess from chunk snippets and do NOT assume "
                "data is missing."
            )
        if tool_tables:
            system_parts.append(
                "For questions about the LARGE tables listed under TOOL USE PROTOCOL, call the "
                "structured-query tools — do not estimate from row text."
            )
        system_parts.append("Use RETRIEVED CONTEXT for prose/document questions, citing [Source N] as needed.")
        expertise_block = _expertise_block(collection_id) if chat_request.scope != "all" else ""
        if expertise_block:
            system_parts.append(
                "When EXPERTISE is provided, use its frameworks, terminology and house style "
                "to shape how you analyse and present the sources, within the rules above."
            )
        # Collection names, descriptions, EXPERTISE and any guide read through
        # get_collection_info are written by people. Say once, in the system
        # voice, what they can and cannot do.
        system_parts.append(authored_text.PROMPT_RULE)
        sections = [" ".join(system_parts), f"COLLECTION OVERVIEW:\n{collection_overview}"]
        if expertise_block:
            sections.append(expertise_block)
        if inline_block:
            sections.append(inline_block)

        agent_context = {"collection_id": collection_id, "scope": chat_request.scope,
                         "document_ids": selected_ids}
        executed: list[dict] = []
        tokens = {"input_tokens": 0, "output_tokens": 0, "model": ai_service.quality_model}

        def account(usage: dict | None):
            usage = usage or {}
            tokens["input_tokens"] += usage.get("input_tokens", 0)
            tokens["output_tokens"] += usage.get("output_tokens", 0)
            tokens["model"] = usage.get("model", tokens["model"])

        def run_tools(calls: list[dict]) -> list[dict]:
            results = execute_tool_calls(calls, agent_context=agent_context)
            evidence.observe(results)
            executed.extend(results)
            return results

        answer = ""
        # ---------- agent loop: native tool calling ------------------------
        if provider.supports_native_tools():
            is_anthropic = isinstance(provider, AnthropicProvider)
            tools_spec = _tools_for_depth(
                chat_request, anthropic_tools() if is_anthropic else openai_tools(), tool_tables,
            )
            max_iterations = (4 if tools_spec else 1) if _is_quick(chat_request) else 8
            system_text = "\n\n".join(
                sections
                + ([f"PRE-RETRIEVED CONTEXT (optional primer):\n{context_text}"] if context_text != _NO_CONTEXT else [])
                + ([f"LARGE TABLES AVAILABLE:\n{tables_block}"] if tables_block else [])
            )
            messages = [{"role": m.role, "content": m.content} for m in chat_request.messages]

            for iteration in range(max_iterations):
                turn = provider.complete_with_tools(
                    messages=messages, tools=tools_spec, max_tokens=4096,
                    model=ai_service.quality_model, system=system_text,
                )
                account(turn.get("usage"))
                tool_calls = turn.get("tool_calls") or []
                narration = turn.get("text") or ""
                if not tool_calls:
                    answer = narration.strip()
                    break
                if narration:
                    executed.append({"tool": "_thinking", "args": {"iteration": iteration}, "result": {"text": narration}})
                    yield {"type": "thinking", "text": narration}
                for tc in tool_calls:
                    yield {"type": "tool_start", "tool": tc["name"], "args": tc.get("input") or {}}
                messages.append(turn["assistant_message"])
                results = run_tools([{"tool": tc["name"], **(tc.get("input") or {})} for tc in tool_calls])
                for tc, res in zip(tool_calls, results):
                    yield {"type": "tool_end", "tool": tc["name"], "result": res}
                messages.extend(_tool_result_messages(tool_calls, results, is_anthropic))

            if not answer:
                # Iteration cap hit mid-research: one more pass with no tools
                # so the turn always ends in an answer.
                try:
                    turn = provider.complete_with_tools(
                        messages=messages, tools=[], max_tokens=2048, model=ai_service.quality_model,
                        system=system_text + "\n\nDo not call any more tools. Summarize the final answer.",
                    )
                    account(turn.get("usage"))
                    answer = (turn.get("text") or "").strip()
                except Exception as e:
                    logger.warning(f"Agent final-answer pass failed: {e}")
                answer = answer or _FINAL_ANSWER_FALLBACK

        # ---------- agent loop: prose <tool_call> protocol (Ollama) ---------
        else:
            tools_block = "" if (_is_quick(chat_request) and not tool_tables) else build_tool_use_instructions()
            history_text = "\n\n".join(
                f"{'User' if m.role == 'user' else 'Assistant'}: {m.content}" for m in chat_request.messages[:-1]
            ) or "(Start of conversation)"

            def compose(extra: str = "") -> str:
                parts = sections + [f"RETRIEVED CONTEXT:\n{context_text}"]
                if tables_block:
                    parts.append(f"LARGE TABLES (use SQL tool calls):\n{tables_block}")
                if tools_block:
                    parts.append(tools_block)
                parts += [f"CONVERSATION HISTORY:\n{history_text}", f"User: {latest_query}"]
                if extra:
                    parts.append(extra)
                parts.append("Assistant:")
                return "\n\n".join(parts)

            def ask(extra: str) -> str:
                result = provider.complete(prompt=compose(extra), max_tokens=2048, model=ai_service.quality_model)
                account(result.get("usage"))
                return result["text"]

            suffix = ""
            for iteration in range(5 if tools_block else 1):
                raw = ask(suffix)
                calls = parse_tool_calls(raw)
                narration = strip_tool_calls(raw)
                if not calls:
                    answer = narration
                    break
                if narration:
                    executed.append({"tool": "_thinking", "args": {"iteration": iteration}, "result": {"text": narration}})
                    yield {"type": "thinking", "text": narration}
                for call in calls:
                    yield {"type": "tool_start", "tool": call.get("tool") or "unknown",
                           "args": {k: v for k, v in call.items() if k != "tool"}}
                results = run_tools(calls)
                for call, res in zip(calls, results):
                    yield {"type": "tool_end", "tool": call.get("tool") or "unknown", "result": res}
                suffix = (
                    (suffix + "\n\n" if suffix else "")
                    + f"Assistant (previous turn):\n{raw.strip()}\n\n"
                    + format_results_for_prompt(results)
                    + "\n\nYou may call more tools, or produce the final answer. "
                    + "When done, write the answer with no <tool_call> blocks."
                )
            if not answer and suffix:
                try:
                    answer = strip_tool_calls(ask(suffix + "\n\nDo not call any more tools. Write the final answer now."))
                except Exception as e:
                    logger.warning(f"Agent final-answer pass failed: {e}")
                answer = answer or _FINAL_ANSWER_FALLBACK

        # ---------- answer, sources, follow-ups ----------------------------
        yield {"type": "text", "text": answer}
        sources = _source_dicts(evidence.results, base_url)
        yield {"type": "sources", "sources": sources}

        # After the answer, so the reader never waits on the extra call.
        related, related_usage = _followups(chat_request, provider, ai_service, latest_query, answer, evidence.results)
        account(related_usage)
        if related:
            yield {"type": "related", "questions": related}

        # Only single-query answers are cacheable: a turn that ran tools has
        # dependencies the cache can't fingerprint.
        if cache_ctx and not executed:
            _cache_store(cache_ctx, answer, evidence.results, chat_request.provider, related=related)

        ai_usage = AIUsage(
            features_used=["chat"] + (["reranking"] if rerank_usage else []) + (["structured_tools"] if executed else []),
            reranking=AIUsageDetail(
                input_tokens=rerank_usage.get("input_tokens", 0),
                output_tokens=rerank_usage.get("output_tokens", 0),
                model=rerank_usage.get("model", ai_service.fast_model),
            ) if rerank_usage else None,
            synthesis=AIUsageDetail(**tokens),
            total_input_tokens=tokens["input_tokens"] + rerank_usage.get("input_tokens", 0),
            total_output_tokens=tokens["output_tokens"] + rerank_usage.get("output_tokens", 0),
        )
        yield _done(
            {"input_tokens": ai_usage.total_input_tokens, "output_tokens": ai_usage.total_output_tokens,
             "model": tokens["model"]},
            executed, sources, related, _ai_usage=ai_usage, _answer=answer,
        )

    except HTTPException as e:
        yield {"type": "error", "message": e.detail, "_status": e.status_code}
    except Exception as e:
        logger.exception("Chat turn failed")
        yield {"type": "error", "message": f"Chat failed: {e}", "_status": 500}


def _provider_args(x_ai_key, x_ollama_model, x_anthropic_model, x_openai_model, x_ai_model, x_ai_base_url) -> dict:
    return {"x_ai_key": x_ai_key, "x_ollama_model": x_ollama_model, "x_anthropic_model": x_anthropic_model,
            "x_openai_model": x_openai_model, "x_ai_model": x_ai_model, "x_ai_base_url": x_ai_base_url}


def _budget_response(turn_user):
    """A 429 when the user is over their daily token budget, else None.
    Checked only after the cache: a cache hit spends nothing, so it stays
    available to a capped user."""
    over_budget = check_daily_budget(turn_user)
    if not over_budget:
        return None
    return JSONResponse(status_code=429, content=over_budget,
                        headers={"Retry-After": str(over_budget["retry_after_seconds"])})


@router.post("/api/chat", response_model=ChatResponse)
def chat_with_documents(  # sync: provider round-trips + tool loop run in FastAPI's threadpool
    chat_request: ChatRequest,
    request: Request,
    collection_id: str = "default",
    x_ai_key: str = Header(None),
    x_ollama_model: str = Header(None),
    x_anthropic_model: str = Header(None),
    x_openai_model: str = Header(None),
    x_ai_model: str = Header(None),
    x_ai_base_url: str = Header(None),
) -> ChatResponse:
    """
    Chat with your indexed documents using conversational AI.

    Retrieves relevant document chunks based on the latest user message,
    then runs the agent loop on the selected AI provider.

    Supports:
    - scope='current': search only the specified collection
    - scope='all': search all collections and merge results by relevance
    - rerank=True: use AI to rerank retrieved context before generating response
    """
    provider_args = _provider_args(x_ai_key, x_ollama_model, x_anthropic_model, x_openai_model, x_ai_model, x_ai_base_url)
    cache_ctx, cache_entry = _prepare_turn(chat_request, collection_id, _requested_model(chat_request, **provider_args))
    if cache_entry is None:
        over_budget = _budget_response(get_request_user())
        if over_budget:
            return over_budget

    done = None
    for event in _run_turn(chat_request, collection_id, str(request.base_url).rstrip("/"),
                           provider_args, cache_ctx, cache_entry):
        if event["type"] == "error":
            raise HTTPException(status_code=event["_status"], detail=event["message"])
        if event["type"] == "done":
            done = event
    if done is None:
        raise HTTPException(status_code=500, detail="Chat failed: no answer produced")
    return ChatResponse(
        message=ChatMessage(role="assistant", content=done["_answer"]),
        sources=[ChatSource(**s) for s in done["sources"]],
        ai_usage=done.get("_ai_usage"),
        structured_results=done["structured_results"] or None,
        cached=done["cached"],
        cached_question=done.get("cached_question"),
        cached_similarity=done.get("cached_similarity"),
        related_questions=done["related_questions"],
        depth=done["depth"],
    )


@router.post("/api/chat/stream", tags=["chat"])
async def chat_stream_endpoint(
    chat_request: ChatRequest,
    request: Request,
    collection_id: str = "default",
    x_ai_key: str = Header(None),
    x_ollama_model: str = Header(None),
    x_anthropic_model: str = Header(None),
    x_openai_model: str = Header(None),
    x_ai_model: str = Header(None),
    x_ai_base_url: str = Header(None),
):
    """
    Streaming version of /api/chat. Returns text/event-stream (SSE).

    Events (each line "data: <json>\\n\\n"): tool_start, tool_end, thinking,
    text_delta, sources, related, done, error — see `_run_turn`.
    """
    provider_args = _provider_args(x_ai_key, x_ollama_model, x_anthropic_model, x_openai_model, x_ai_model, x_ai_base_url)
    # Cache and budget are settled before the stream starts: the budget can
    # still answer with a real HTTP 429, impossible once SSE has begun.
    cache_ctx, cache_entry = await asyncio.to_thread(
        _prepare_turn, chat_request, collection_id, _requested_model(chat_request, **provider_args),
    )
    if cache_entry is None:
        over_budget = _budget_response(get_request_user())
        if over_budget:
            return over_budget

    turn = _run_turn(chat_request, collection_id, str(request.base_url).rstrip("/"),
                     provider_args, cache_ctx, cache_entry)

    def sse(payload: dict) -> str:
        return f"data: {json.dumps({k: v for k, v in payload.items() if not k.startswith('_')}, default=str)}\n\n"

    async def generate():
        exhausted = object()
        try:
            while True:
                # Every step of the turn is blocking provider/index work — run
                # it off the event loop, one event at a time.
                event = await asyncio.to_thread(next, turn, exhausted)
                if event is exhausted:
                    break
                if event["type"] != "text" or event.get("_whole"):
                    yield sse(event if event["type"] != "text" else {"type": "text_delta", "delta": event["text"]})
                    continue
                words = event["text"].split(" ")
                for i, word in enumerate(words):
                    yield sse({"type": "text_delta", "delta": word + (" " if i < len(words) - 1 else "")})
                    await asyncio.sleep(0.008)
        except Exception as e:
            logger.exception("Stream chat failed")
            yield sse({"type": "error", "message": str(e)})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Access-Control-Allow-Origin": "*"},
    )


# ---------------------------------------------------------------------------
# Starter questions: what to ask a collection you just opened
# ---------------------------------------------------------------------------
# One fast-model call per (collection, corpus version, selection, model),
# remembered in-process so reopening the tab costs nothing until the
# sources change. Reads go through get_indexer (the private-collections
# read gate) and hidden/quarantined documents are never sampled.

_STARTERS_CACHE: dict[str, tuple[float, list[str]]] = {}
_STARTERS_TTL_SECONDS = 24 * 3600
_STARTERS_MAX_ENTRIES = 256
_STARTERS_SAMPLE_DOCS = 6


def _build_provider(provider_name: str, x_ai_key, x_ollama_model, x_anthropic_model,
                    x_openai_model, x_ai_model, x_ai_base_url):
    """The provider a request runs on, resolved exactly as /api/chat does."""
    try:
        if provider_name == "ollama":
            # No hard-coded fallback model here: a request that names none
            # must fall through to the deployment default (AI_MODEL, applied
            # inside create_provider) — a hard-coded name would win over it
            # and send an MCP ask_documents call to a model nobody pulled.
            extra: dict = {}
            model = x_ai_model or x_ollama_model
            if model:
                extra["model"] = model
            if x_ai_base_url:
                extra["base_url"] = x_ai_base_url
            return create_provider("ollama", **extra)
        key = resolve_ai_key(provider_name, x_ai_key)
        if not key and provider_name != "openai_compatible":
            raise HTTPException(
                status_code=400,
                detail=f"API key required for {provider_name} — pass X-AI-Key or store a team key via /api/agent/config",
            )
        extra = {}
        model = x_ai_model or x_anthropic_model or x_openai_model
        if model:
            extra["model"] = model
        if x_ai_base_url:
            extra["base_url"] = x_ai_base_url
        return create_provider(provider_name, key, **extra)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to initialize AI provider: {e}")


def _sample_passages(indexer, selected_ids: list[str] | None) -> list[dict]:
    """The opening passage of a few recent, servable documents."""
    from services.governance import is_hidden

    docs = [d for d in indexer.list_documents() if not is_hidden(d)]
    if selected_ids:
        wanted = set(selected_ids)
        docs = [d for d in docs if d.get("document_id") in wanted]
    metadata = indexer.vector_store.metadata_store
    samples = []
    for doc in docs[:_STARTERS_SAMPLE_DOCS]:
        try:
            rowids = metadata.get_document_chunk_rowids(doc["document_id"])[:1]
            chunks = metadata.get_chunks_by_rowids(rowids) if rowids else {}
            text = next((c.get("text") for c in chunks.values() if c.get("text")), "")
        except Exception as exc:  # noqa: BLE001 - one bad document must not block the rest
            logger.debug("Starter sample skipped for %s: %s", doc.get("document_id"), exc)
            text = ""
        samples.append({"filename": doc.get("filename"), "text": text or ""})
    return samples


@router.post(
    "/api/chat/starters",
    response_model=StarterQuestionsResponse,
    tags=["chat"],
    summary="Suggested opening questions for a collection",
)
def starter_questions_endpoint(  # sync: provider round-trip runs in the threadpool
    body: StarterQuestionsRequest,
    collection_id: str = "default",
    x_ai_key: str = Header(None),
    x_ollama_model: str = Header(None),
    x_anthropic_model: str = Header(None),
    x_openai_model: str = Header(None),
    x_ai_model: str = Header(None),
    x_ai_base_url: str = Header(None),
) -> StarterQuestionsResponse:
    """Questions the collection can actually answer, written from its overview
    and a sample of its passages. Cached per corpus version and selection."""
    started = time.time()
    try:
        indexer = get_indexer(collection_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    selected = sorted({str(d).strip() for d in (body.document_ids or []) if str(d).strip()}) or None
    model = _requested_model(body, x_ai_model, x_ollama_model, x_anthropic_model,
                             x_openai_model, x_ai_base_url)
    key = "|".join([collection_id, _corpus_version(collection_id), ",".join(selected or []),
                    body.provider, model])
    hit = _STARTERS_CACHE.get(key)
    if hit and time.time() - hit[0] < _STARTERS_TTL_SECONDS:
        return StarterQuestionsResponse(questions=list(hit[1]), cached=True)

    user = get_request_user()
    over_budget = check_daily_budget(user)
    if over_budget:
        return JSONResponse(status_code=429, content=over_budget,
                            headers={"Retry-After": str(over_budget["retry_after_seconds"])})

    provider = _build_provider(body.provider, x_ai_key, x_ollama_model, x_anthropic_model,
                               x_openai_model, x_ai_model, x_ai_base_url)
    ai_service = AIService(provider=provider)

    samples = _sample_passages(indexer, selected)
    if not samples:
        return StarterQuestionsResponse(questions=[], cached=False)
    try:
        overview = _build_collection_overview([collection_id], selected)
    except Exception as e:
        logger.warning(f"Starters: collection overview unavailable: {e}")
        overview = ""

    out = _starter_questions(provider, ai_service.fast_model, overview, samples)
    questions = out.get("questions") or []
    usage = out.get("usage") or {}
    record_chat_usage(
        user, collection_id, body.provider, usage.get("model", ai_service.fast_model),
        input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
        duration_ms=int((time.time() - started) * 1000),
    )
    if questions:
        if len(_STARTERS_CACHE) >= _STARTERS_MAX_ENTRIES:
            oldest = min(_STARTERS_CACHE, key=lambda k: _STARTERS_CACHE[k][0])
            _STARTERS_CACHE.pop(oldest, None)
        _STARTERS_CACHE[key] = (time.time(), list(questions))
    return StarterQuestionsResponse(questions=questions, cached=False)


# Local File Reference endpoints (v3.1 feature)

@router.post(
    "/api/ai/validate-key",
    summary="Validate an AI provider API key or Ollama model",
    tags=["ai"],
)
def validate_api_key(  # sync: provider round-trip runs in the threadpool
    x_ai_key: str = Header(None),
    x_ai_provider: str = Header("anthropic"),
    x_ollama_model: str = Header(None),
    x_ai_model: str = Header(None),
    x_ai_base_url: str = Header(None),
):
    """
    Validate an API key or Ollama model availability.

    For cloud providers (Anthropic, OpenAI):
    - Pass the key via X-AI-Key header
    - Pass provider via X-AI-Provider header

    For Ollama:
    - Pass 'ollama' via X-AI-Provider header
    - Pass model name via X-Ollama-Model header
    - X-AI-Key is not required
    """
    try:
        if x_ai_provider not in ALL_AI_PROVIDERS:
            return {
                "valid": False,
                "error": f"Invalid provider: {x_ai_provider}. "
                         f"Supported providers: {', '.join(ALL_AI_PROVIDERS)}"
            }

        if x_ai_provider == "ollama":
            model = x_ai_model or x_ollama_model or "llama3.2"
            extra: dict = {"model": model}
            if x_ai_base_url:
                extra["base_url"] = x_ai_base_url
            provider = create_provider("ollama", **extra)
        elif x_ai_provider == "openai_compatible":
            from config import settings
            if not x_ai_base_url and not (
                settings.ai_provider == "openai_compatible" and settings.ai_base_url
            ):
                return {"valid": False, "error": "base_url required for openai_compatible provider"}
            provider = create_provider(
                "openai_compatible",
                api_key=x_ai_key,
                **({"base_url": x_ai_base_url} if x_ai_base_url else {}),
                **({"model": x_ai_model} if x_ai_model else {}),
            )
        else:
            if not x_ai_key:
                return {"valid": False, "error": "API key is required for cloud providers"}
            extra = {}
            if x_ai_model:
                extra["model"] = x_ai_model
            provider = create_provider(x_ai_provider, x_ai_key, **extra)

        valid = provider.validate()
        if not valid:
            # The provider's API rejected the key — say so explicitly, so a bad
            # key doesn't read as "the integration is broken".
            label = getattr(provider, "PROVIDER_LABEL", x_ai_provider)
            hint = getattr(provider, "KEY_HINT", "")
            msg = f"{label} rejected this API key."
            if hint:
                msg += f" Double-check it (or create a new one) at {hint}"
            return {"valid": False, "error": msg}
        return {"valid": True, "error": None}
    except Exception as e:
        error_str = str(e)
        logger.error(f"API key validation error for {x_ai_provider}: {e}")

        # Provide more helpful error messages
        if x_ai_provider == "openai_compatible":
            from openai import AuthenticationError, PermissionDeniedError, NotFoundError, APIConnectionError
            if isinstance(e, AuthenticationError):
                return {"valid": False, "error": "Endpoint rejected the API key (HTTP 401). Check the credential for this endpoint."}
            if isinstance(e, PermissionDeniedError):
                return {"valid": False, "error": "Endpoint denied access (HTTP 403). Check the key's model permissions."}
            if isinstance(e, NotFoundError):
                return {"valid": False, "error": "Endpoint returned HTTP 404. Check the base URL (/v1 where required) and model ID."}
            if isinstance(e, APIConnectionError):
                return {"valid": False, "error": "Could not connect to the endpoint. Check its address, TLS CA trust, and any required client certificate."}
        if "quota" in error_str.lower() or "insufficient_quota" in error_str.lower():
            return {"valid": False, "error": "Your API key has exceeded its quota. Please add credits to your account."}
        elif "rate" in error_str.lower() and "limit" in error_str.lower():
            return {"valid": False, "error": "Rate limit exceeded. Please wait a moment and try again."}
        elif any(kw in error_str.lower() for kw in ("connection error", "connect error", "connection refused", "name or service not known", "failed to establish")):
            host = "ollama.com" if x_ai_provider == "ollama_cloud" else "the provider's API"
            return {"valid": False, "error": f"Could not reach {host}. Check your network connection and try again."}
        elif "model" in error_str.lower() and any(kw in error_str.lower() for kw in ("not found", "does not exist", "not_found", "unknown", "invalid model", "no access")):
            tried = x_ai_model or "the default model"
            return {"valid": False, "error": f"The key was accepted, but the provider rejected the model ({tried}). Pick a model your plan includes and test again."}
        else:
            return {"valid": False, "error": error_str}


@router.post(
    "/api/ai/models",
    summary="List available models for an AI provider",
    tags=["ai"],
)
def list_provider_models(  # sync: provider round-trip runs in the threadpool
    x_ai_key: str = Header(None),
    x_ai_provider: str = Header("anthropic"),
    x_ai_base_url: str = Header(None),
):
    """
    List the models an AI provider offers, using the caller's credentials.

    Same header contract as /api/ai/validate-key. Providers that cannot
    enumerate models return an empty list — the frontend then falls back to
    its curated recommendations. Missing X-AI-Key falls back to a
    server-stored team key for that provider, if one exists.
    """
    try:
        if x_ai_provider not in ALL_AI_PROVIDERS:
            return {"models": [], "error": f"Invalid provider: {x_ai_provider}"}

        api_key = x_ai_key
        if not api_key and x_ai_provider != "ollama":
            from services.app_database import app_db
            api_key = app_db.get_agent_api_key(x_ai_provider) or None

        if x_ai_provider == "ollama":
            extra: dict = {}
            if x_ai_base_url:
                extra["base_url"] = x_ai_base_url
            provider = create_provider("ollama", **extra)
        elif x_ai_provider == "openai_compatible":
            from config import settings
            if not x_ai_base_url and not (
                settings.ai_provider == "openai_compatible" and settings.ai_base_url
            ):
                return {"models": [], "error": "base_url required for openai_compatible provider"}
            provider = create_provider(
                "openai_compatible",
                api_key=api_key or "",
                **({"base_url": x_ai_base_url} if x_ai_base_url else {}),
            )
        else:
            if not api_key:
                return {"models": [], "error": "API key is required for cloud providers"}
            provider = create_provider(x_ai_provider, api_key)

        return {"models": provider.list_models(), "error": None}
    except Exception as e:
        logger.warning(f"Model listing failed for {x_ai_provider}: {e}")
        return {"models": [], "error": str(e)}


@router.get(
    "/api/ai/deployment",
    summary="The AI provider this deployment configured for everyone",
    tags=["ai"],
)
async def get_deployment_ai_default():
    """
    What AI_PROVIDER / AI_BASE_URL / AI_MODEL describe: the model an operator
    wired into this deployment, so a browser that has configured nothing can
    still chat. The frontend lists it as an already-connected provider and
    sends no credentials for it — the server fills them in.

    Never returns AI_API_KEY, only whether one is on file. The base URL is
    returned: it is what the UI shows people so they can see which endpoint
    their questions go to, and on an on-prem install it is an internal
    hostname the caller is already inside the network of.
    """
    from config import settings

    provider = (settings.ai_provider or "").strip()
    if not provider:
        return {"configured": False, "offline_mode": settings.offline_mode}

    base_url = settings.ai_base_url or (
        settings.ollama_base_url if provider == "ollama" else ""
    )
    return {
        "configured": True,
        "provider": provider,
        "label": settings.ai_provider_label or _DEFAULT_PROVIDER_LABELS.get(provider, provider),
        "model": settings.ai_model,
        "base_url": base_url,
        "key_configured": bool(settings.ai_api_key),
        "offline_mode": settings.offline_mode,
    }


# Shown when AI_PROVIDER_LABEL is unset. "openai_compatible" is an interop
# shape, not a product name, so a private endpoint gets a neutral label
# rather than a vendor's.
_DEFAULT_PROVIDER_LABELS = {
    "openai_compatible": "Private endpoint",
    "ollama": "Ollama",
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "google": "Google Gemini",
    "grok": "Grok (xAI)",
    "github": "GitHub Models",
    "openrouter": "OpenRouter",
    "ollama_cloud": "Ollama Cloud",
}


@router.get(
    "/api/agent/config",
    summary="Get agent API configuration",
    tags=["agent"],
)
async def get_agent_config():
    """
    Get the current agent API configuration.

    Returns which providers have API keys configured (keys are masked for security).
    """
    from services.app_database import app_db
    return app_db.get_agent_config()


@router.post(
    "/api/agent/config",
    summary="Configure agent API key",
    tags=["agent"],
)
async def set_agent_config(
    provider: str,
    api_key: str,
):
    """
    Store an API key for agent use.

    This allows the /api/ask endpoint to work without requiring
    the X-AI-Key header on every request.

    Args:
        provider: AI provider name (anthropic, openai)
        api_key: The API key to store

    Note: Keys are stored server-side. For security, ensure your
    Clio instance is properly secured. A stored key becomes shared
    deployment state (everyone's chat can spend it), so under private
    collections this is admin-only — same trust rule as POST /api/config.
    """
    from api.deps import require_admin
    require_admin("store a team API key")
    from services.app_database import app_db

    if provider not in CLOUD_AI_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid provider: {provider}. Use one of {', '.join(CLOUD_AI_PROVIDERS)}.",
        )

    # Validate the key before storing
    try:
        test_provider = create_provider(provider, api_key)
        valid = test_provider.validate()
        if not valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"API key validation failed for {provider}",
            )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"API key validation failed: {e}",
        )

    app_db.set_agent_api_key(provider, api_key)
    return {"success": True, "message": f"API key configured for {provider}"}


@router.delete(
    "/api/agent/config/{provider}",
    summary="Remove agent API key",
    tags=["agent"],
)
async def delete_agent_config(provider: str):
    """
    Remove a stored API key. Admin-only under private collections —
    removing the team key breaks chat for everyone relying on it.

    Args:
        provider: AI provider name (anthropic, openai)
    """
    from api.deps import require_admin
    require_admin("remove the team API key")
    from services.app_database import app_db

    if provider not in CLOUD_AI_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid provider: {provider}",
        )

    app_db.delete_agent_api_key(provider)
    return {"success": True, "message": f"API key removed for {provider}"}
