"""ask_documents: the grounded-answer MCP tool as a thin adapter over the chat loop.

The chat pipeline itself is covered elsewhere; here `_run_turn` is replaced
by a scripted generator so the tests check the adapter's contract — gating
on a deployment model, collection authorization, the payload shape, cache
and budget handling — without a provider.
"""

from __future__ import annotations

import pytest

import services.mcp_server as mcp
from config import settings


@pytest.fixture()
def deployment_model(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "ollama", raising=False)
    monkeypatch.setattr(settings, "ai_model", "test-model", raising=False)
    monkeypatch.setattr(settings, "mcp_public_url", "https://clio.example.org/mcp", raising=False)
    monkeypatch.setattr(mcp, "_resolve_collection_id", lambda explicit=None: "col-1")
    monkeypatch.setattr(mcp.collection_service, "get_collection", lambda cid: {"id": cid, "name": "Ops runbooks"})
    monkeypatch.setattr(mcp, "_corpus_version", lambda cid: "v7")
    monkeypatch.setattr(mcp, "_research_gate_reset", lambda: None)


def _scripted_turn(events, seen):
    def _run_turn(chat_request, collection_id, base_url, provider_args, cache_ctx, cache_entry):
        seen.update(request=chat_request, collection_id=collection_id, base_url=base_url,
                    provider_args=provider_args, cache_ctx=cache_ctx, cache_entry=cache_entry)
        yield from events
    return _run_turn


def test_refuses_without_a_deployment_model(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "", raising=False)
    with pytest.raises(ValueError, match="AI_PROVIDER"):
        mcp.ask_documents_sync("what is the backup window")


def test_answer_payload_shape(deployment_model, monkeypatch):
    from api import chat as chat_api

    seen: dict = {}
    done = {
        "type": "done", "depth": "quick", "cached": False,
        "usage": {"input_tokens": 120, "output_tokens": 40, "model": "test-model"},
        "structured_results": [{"tool": "_thinking"}, {"tool": "list_tables"}],
        "sources": [{"filename": "runbook.md", "page_number": 2, "document_id": "d1",
                     "text_snippet": "Backups run nightly at 02:00 UTC." * 50, "sensitivity": "internal"}],
        "_answer": "Backups run nightly at 02:00 UTC [Source 1].",
    }
    monkeypatch.setattr(chat_api, "_run_turn", _scripted_turn(
        [{"type": "text", "text": "..."}, {"type": "sources", "sources": done["sources"]}, done], seen))
    monkeypatch.setattr(chat_api, "_prepare_turn", lambda req, cid, hint: ("ctx", None))
    monkeypatch.setattr(chat_api, "check_daily_budget", lambda user: None)

    out = mcp.ask_documents_sync("When do backups run?", collection_id="Ops runbooks", top_k=5,
                                 document_ids=["d1", " ", "d2"])

    assert out["answer"].startswith("Backups run nightly")
    assert out["collection_id"] == "col-1" and out["collection_name"] == "Ops runbooks"
    assert out["corpus_version"] == "v7"
    assert out["sources"][0]["n"] == 1 and out["sources"][0]["document_id"] == "d1"
    assert out["sources"][0]["sensitivity"] == "internal"
    assert len(out["sources"][0]["excerpt"]) <= settings.mcp_max_source_length
    assert out["usage"] == {"input_tokens": 120, "output_tokens": 40}
    assert out["model"] == "test-model" and out["tool_calls"] == 1
    assert out["document_ids"] == ["d1", "d2"]

    request = seen["request"]
    assert request.provider == "ollama" and request.depth == "quick" and request.top_k == 5
    assert request.document_ids == ["d1", "d2"] and request.related is False
    assert seen["collection_id"] == "col-1"
    # Source links point at the app, never at the /mcp endpoint.
    assert seen["base_url"] == "https://clio.example.org"
    assert all(v is None for v in seen["provider_args"].values())


def test_turn_error_becomes_tool_error(deployment_model, monkeypatch):
    from api import chat as chat_api

    monkeypatch.setattr(chat_api, "_run_turn", _scripted_turn(
        [{"type": "error", "message": "API key required for ollama", "_status": 400}], {}))
    monkeypatch.setattr(chat_api, "_prepare_turn", lambda req, cid, hint: (None, None))
    monkeypatch.setattr(chat_api, "check_daily_budget", lambda user: None)
    with pytest.raises(ValueError, match="API key required"):
        mcp.ask_documents_sync("anything")


def test_budget_is_checked_only_for_uncached_answers(deployment_model, monkeypatch):
    from api import chat as chat_api

    cached_done = {"type": "done", "depth": "quick", "cached": True, "sources": [], "structured_results": [],
                   "usage": {"input_tokens": 0, "output_tokens": 0, "model": "answer-cache"}, "_answer": "cached"}
    monkeypatch.setattr(chat_api, "_run_turn", _scripted_turn([cached_done], {}))
    monkeypatch.setattr(chat_api, "_prepare_turn", lambda req, cid, hint: ("ctx", {"answer": "cached"}))
    monkeypatch.setattr(chat_api, "check_daily_budget",
                        lambda user: {"detail": "daily budget spent", "retry_after_seconds": 60})
    out = mcp.ask_documents_sync("again?")
    assert out["cached"] is True and out["answer"] == "cached"

    monkeypatch.setattr(chat_api, "_prepare_turn", lambda req, cid, hint: ("ctx", None))
    with pytest.raises(ValueError, match="daily budget"):
        mcp.ask_documents_sync("something new")


def test_rejects_bad_inputs(deployment_model):
    with pytest.raises(ValueError, match="empty"):
        mcp.ask_documents_sync("   ")
    with pytest.raises(ValueError, match="depth"):
        mcp.ask_documents_sync("q", depth="deep")


def test_tool_is_absent_without_a_deployment_model():
    """The registry is built at import time from AI_PROVIDER; the test
    process runs without one, so the catalog must not advertise it."""
    import asyncio

    names = {t["name"] for t in asyncio.run(mcp.mcp_catalog())["tools"]}
    assert ("ask_documents" in names) == mcp._ASK_AVAILABLE
    assert ("ask_documents" in mcp._SERVER_INSTRUCTIONS) == mcp._ASK_AVAILABLE
