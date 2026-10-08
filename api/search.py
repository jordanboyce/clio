"""Semantic search and embedding endpoints."""

import logging
import json
import asyncio

from services.embedder import EmbeddingUnavailable
from fastapi import Header, HTTPException, status, Request

from services.ai_service import AIService, create_provider
from services.collection_overview import build_collection_overview as _build_collection_overview
from services.structured_chat import (
    build_structured_context,
    collect_structured_tables,
)
from models.schemas import (
    SearchRequest,
    SearchResponse,
)

from fastapi import APIRouter
from api.deps import (
    get_indexer,
    resolve_ai_key,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/search",
    response_model=SearchResponse,
    summary="Search indexed documents",
    tags=["documents"],
)
async def search_documents(
    search_request: SearchRequest,
    request: Request,
    collection_id: str = "default",
    x_ai_key: str = Header(None),
    x_ollama_model: str = Header(None),
    x_anthropic_model: str = Header(None),
    x_openai_model: str = Header(None),
    x_ai_model: str = Header(None),
    x_ai_base_url: str = Header(None),
) -> SearchResponse:
    """
    Perform semantic similarity search over indexed documents.

    Args:
        search_request: Search query and options
        collection_id: Collection to search (default: "default")

    Optionally enable AI enhancements by including 'ai' options in the request body.
    Supports Anthropic, OpenAI, and Ollama providers.

    For cloud providers (Anthropic, OpenAI):
    - Pass API key via X-AI-Key header
    - Set provider in request body ai.provider

    For Ollama:
    - Pass model name via X-Ollama-Model header (e.g., llama3.2)
    - Set provider to 'ollama' in request body ai.provider
    - No API key required
    """
    try:
        import time
        start_time = time.time()

        # Get indexer for the collection
        try:
            indexer = get_indexer(collection_id)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            )

        # Build AI service if AI options requested
        ai_service = None
        ai_options = search_request.ai
        ai_provider_used = None

        if ai_options:
            try:
                if ai_options.provider == "ollama":
                    model = x_ai_model or x_ollama_model or "llama3.2"
                    extra: dict = {"model": model}
                    if x_ai_base_url:
                        extra["base_url"] = x_ai_base_url
                    provider = create_provider("ollama", **extra)
                    ai_service = AIService(provider=provider)
                    ai_provider_used = "ollama"
                else:
                    resolved_key = resolve_ai_key(ai_options.provider, x_ai_key)
                    if not resolved_key and ai_options.provider != "openai_compatible":
                        logger.warning(f"AI key required for provider: {ai_options.provider}")
                    else:
                        extra = {}
                        model = x_ai_model or x_anthropic_model or x_openai_model
                        if model:
                            extra["model"] = model
                        if x_ai_base_url:
                            extra["base_url"] = x_ai_base_url
                        provider = create_provider(ai_options.provider, resolved_key, **extra)
                        ai_service = AIService(provider=provider)
                        ai_provider_used = ai_options.provider
            except Exception as e:
                logger.warning(f"Failed to create AI service: {e}")

        # Build collection overview so AI synthesis can answer meta-questions
        # like "how many sources are in this collection?"
        collection_overview = None
        structured_context_str = None
        skip_filenames: set = set()
        if ai_service and ai_options and ai_options.synthesize:
            try:
                collection_overview = _build_collection_overview([collection_id])
            except Exception as e:
                logger.warning(f"Failed to build collection overview for search: {e}")

            # Inline small CSV/XLSX tables in full so synthesis never has to
            # rely on top-K chunk recall for numeric questions. Large tables
            # stay out of synthesis (users should use the Chat tab which runs
            # the SQL tool loop for those).
            try:
                s_tables, s_stores = collect_structured_tables([collection_id])
                if s_tables:
                    ctx = build_structured_context(s_tables, s_stores)
                    if ctx["inline_block"]:
                        structured_context_str = ctx["inline_block"]
                        skip_filenames = ctx["inlined_filenames"]
            except Exception as e:
                logger.warning(f"Failed to build structured context for search: {e}")

        # Run the search in a worker thread: query embedding, FAISS, the
        # optional reranker, and AI synthesis are all synchronous and would
        # otherwise block the event loop for every other request (UI + MCP).
        search_result = await asyncio.to_thread(
            indexer.search,
            query=search_request.query,
            top_k=search_request.top_k,
            ai_service=ai_service,
            ai_options=ai_options,
            mode=search_request.mode,
            semantic_weight=search_request.semantic_weight,
            collection_overview=collection_overview,
            structured_context=structured_context_str,
            skip_filenames=skip_filenames or None,
            filters=search_request.filters.to_dict() if search_request.filters else None,
        )

        results = search_result["results"]

        # Add URLs to each result (include collection_id for proper routing)
        # and resolve the sensitivity label in force (document override,
        # else the collection's).
        from services.collection_service import collection_service
        from services.governance import effective_sensitivity
        collection = collection_service.get_collection(collection_id)
        base_url = str(request.base_url).rstrip('/')
        for result in results:
            result.pdf_url = f"{base_url}/documents/{result.document_id}/pdf?collection_id={collection_id}"
            result.page_url = f"{base_url}/documents/{result.document_id}/pdf?collection_id={collection_id}#page={result.page_number}"
            result.sensitivity = effective_sensitivity(collection, result.sensitivity)

        execution_time_ms = int((time.time() - start_time) * 1000)

        # Save to search history
        from services.app_database import app_db
        try:
            results_json = json.dumps([{
                "document_id": r.document_id,
                "filename": r.filename,
                "page_number": r.page_number,
                "chunk_id": r.chunk_id,
                "text": r.text_snippet,
                "similarity": r.similarity_score,
                "pdf_url": r.pdf_url,
                "page_url": r.page_url
            } for r in results])

            from middleware.user_context import get_request_user
            app_db.add_search_history(
                query=search_request.query,
                top_k=search_request.top_k,
                results_count=len(results),
                ai_provider=ai_provider_used,
                ai_used=ai_service is not None,
                results_json=results_json,
                execution_time_ms=execution_time_ms,
                user_id=get_request_user(),
                collection_id=collection_id,
            )
        except Exception as e:
            logger.warning(f"Failed to save search history: {e}")

        return SearchResponse(
            query=search_request.query,
            results=results,
            total_results=len(results),
            synthesis=search_result.get("synthesis"),
            ai_usage=search_result.get("ai_usage"),
        )

    except EmbeddingUnavailable:
        # The model is not here yet (firewall, missing backend): 503 with the
        # reason via the app-level handler, not a 500.
        raise
    except Exception as e:
        logger.error(f"Search failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {str(e)}",
        )


