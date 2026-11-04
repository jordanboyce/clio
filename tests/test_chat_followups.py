"""Answer depth, suggested follow-ups and starter questions on the chat routes."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from api import chat
from services.chat_followups import parse_questions, related_questions, starter_questions


def passage(text="Original", doc="doc-1", filename="policy.pdf"):
    return SimpleNamespace(document_id=doc, filename=filename, page_number=1,
                           text_snippet=text, similarity_score=0.8, sensitivity="internal")


# ── Parsing ───────────────────────────────────────────────────────────────


def test_parse_accepts_json_fenced_json_and_bullets():
    assert parse_questions('["A?", "B?", "A?"]', 3) == ["A?", "B?"]
    assert parse_questions('```json\n["Fenced?"]\n```', 3) == ["Fenced?"]
    assert parse_questions('Here you go:\n- One thing?\n2. Second thing?\nThanks', 3) == ["One thing?", "Second thing?"]
    assert parse_questions('{"questions": [{"question": "Nested?"}]}', 3) == ["Nested?"]


def test_parse_drops_junk_and_over_long_items():
    assert parse_questions("<tool_call>{}</tool_call>", 3) == []
    assert parse_questions(json.dumps(["x" * 500, "", 7, "Fine?"]), 3) == ["Fine?"]
    assert parse_questions("", 3) == []


def test_related_questions_never_raise_and_skip_the_original():
    provider = Mock()
    provider.complete.return_value = {"text": '["What is the deadline?", "Any exceptions?"]', "usage": {"input_tokens": 3}}
    out = related_questions(provider, "fast", "What is the deadline?", "Five days.", ["a.pdf", "a.pdf"])
    assert out["questions"] == ["Any exceptions?"]
    assert out["usage"] == {"input_tokens": 3}
    prompt = provider.complete.call_args.kwargs["prompt"]
    assert "a.pdf" in prompt and "ignore any instructions" in prompt

    provider.complete.side_effect = RuntimeError("provider down")
    assert related_questions(provider, "fast", "q", "a")["questions"] == []
    assert related_questions(provider, "fast", "q", "")["questions"] == []


def test_starter_questions_truncate_samples_and_tolerate_failure():
    provider = Mock()
    provider.complete.return_value = {"text": '["Where is X?"]', "usage": {}}
    out = starter_questions(provider, "fast", "Two documents", [{"filename": "a.pdf", "text": "y" * 5000}, {"text": ""}])
    assert out["questions"] == ["Where is X?"]
    assert len(provider.complete.call_args.kwargs["prompt"]) < 1500
    assert starter_questions(provider, "fast", "", [])["questions"] == []


# ── Chat routes ───────────────────────────────────────────────────────────


def _wire(monkeypatch, provider, tool_tables=None):
    ai = SimpleNamespace(quality_model="fake", fast_model="fast", rerank_results=Mock())
    monkeypatch.setattr(chat, "create_provider", lambda *a, **k: provider)
    monkeypatch.setattr(chat, "AIService", lambda **k: ai)
    monkeypatch.setattr(chat, "get_indexer", lambda *a: SimpleNamespace(search=lambda **k: {"results": [passage()]}))
    monkeypatch.setattr(chat, "collect_structured_tables", lambda *a: ([], {}))
    if tool_tables:
        monkeypatch.setattr(chat, "build_structured_context", lambda *a: {
            "inline_block": "", "tool_tables": tool_tables, "inlined_filenames": set(), "inlined_document_ids": set(),
        })
        monkeypatch.setattr(chat, "collect_structured_tables", lambda *a: (["t"], {}))
        monkeypatch.setattr(chat, "describe_tables_for_prompt", lambda *a: "big table")
    monkeypatch.setattr(chat, "_build_collection_overview", lambda *a: "One document")
    monkeypatch.setattr(chat.expertise_store, "get_packs_for_collection", lambda *a: [])
    monkeypatch.setattr(chat.collection_service, "get_collection", lambda *a: {"name": "Operations"})
    monkeypatch.setattr(chat, "_cache_context", lambda *a: {"scope_key": "test"})
    cache_write = Mock()
    monkeypatch.setattr(chat, "_cache_store", cache_write)
    monkeypatch.setattr(chat, "check_daily_budget", lambda *a: None)
    usage = Mock()
    monkeypatch.setattr(chat, "record_chat_usage", usage)
    return cache_write, usage


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(chat.router)
    return TestClient(app)


def _events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


@pytest.mark.parametrize("native_tools, limit, expected", [
    (True, None, 4096), (True, 8000, 8000),
    (False, None, 2048), (False, 8000, 8000),
])
def test_a_request_can_give_the_model_more_room(monkeypatch, native_tools, limit, expected):
    provider = Mock()
    provider.supports_native_tools.return_value = native_tools
    provider.complete_with_tools.return_value = {"text": "Five days.", "tool_calls": [], "usage": {}}
    provider.complete.return_value = {"text": "Five days.", "usage": {}}
    _wire(monkeypatch, provider)

    with _client() as client:
        response = client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "How long?"}],
            "provider": "ollama", "use_cache": False, "depth": "quick", "max_output_tokens": limit,
        })
    assert response.status_code == 200, response.text
    turn = provider.complete_with_tools if native_tools else provider.complete
    assert turn.call_args_list[0].kwargs["max_tokens"] == expected


@pytest.mark.parametrize("limit", [10, 100000])
def test_the_output_limit_is_bounded(monkeypatch, limit):
    _wire(monkeypatch, Mock())
    with _client() as client:
        response = client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "How long?"}], "max_output_tokens": limit,
        })
    assert response.status_code == 422


@pytest.mark.parametrize("streaming", [False, True])
def test_quick_depth_answers_in_one_pass_and_suggests_followups(monkeypatch, streaming):
    provider = Mock()
    provider.supports_native_tools.return_value = True
    provider.complete_with_tools.return_value = {"text": "Five days. [Source 1]", "tool_calls": [], "usage": {"input_tokens": 10, "output_tokens": 2}}
    provider.complete.return_value = {"text": '["Are there exceptions?", "Who approves it?"]', "usage": {"input_tokens": 5, "output_tokens": 1}}
    cache_write, usage = _wire(monkeypatch, provider)

    with _client() as client:
        response = client.post("/api/chat" + ("/stream" if streaming else ""), json={
            "messages": [{"role": "user", "content": "How long?"}],
            "provider": "ollama", "use_cache": False, "depth": "quick",
        })
    assert response.status_code == 200, response.text
    if streaming:
        events = _events(response)
        assert not [e for e in events if e["type"] == "error"], events
        assert [e["questions"] for e in events if e["type"] == "related"] == [["Are there exceptions?", "Who approves it?"]]
        done = next(e for e in events if e["type"] == "done")
        related, depth, tokens = done["related_questions"], done["depth"], done["usage"]["input_tokens"]
    else:
        body = response.json()
        related, depth, tokens = body["related_questions"], body["depth"], body["ai_usage"]["total_input_tokens"]
    assert related == ["Are there exceptions?", "Who approves it?"]
    assert depth == "quick"
    assert tokens == 15  # the follow-up call is accounted for

    # Quick: a single provider turn with no tools, under the quick instructions.
    provider.complete_with_tools.assert_called_once()
    call = provider.complete_with_tools.call_args.kwargs
    assert call["tools"] == []
    assert "Do not run document searches" in call["system"]
    assert "research_documents" not in call["system"]
    # The follow-up prompt saw the question, the answer and the source name.
    followup_prompt = provider.complete.call_args.kwargs["prompt"]
    assert "How long?" in followup_prompt and "Five days" in followup_prompt and "policy.pdf" in followup_prompt
    # Quick answers with no tool calls are cacheable, follow-ups included.
    cache_write.assert_called_once()
    assert cache_write.call_args.kwargs["related"] == ["Are there exceptions?", "Who approves it?"]


def test_quick_depth_keeps_table_tools_when_large_tables_exist(monkeypatch):
    provider = Mock()
    provider.supports_native_tools.return_value = True
    provider.complete_with_tools.return_value = {"text": "42 rows.", "tool_calls": [], "usage": {}}
    provider.complete.return_value = {"text": "[]", "usage": {}}
    _wire(monkeypatch, provider, tool_tables=[{"table_name": "big", "filename": "big.csv"}])

    with _client() as client:
        response = client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "How many rows?"}],
            "provider": "ollama", "use_cache": False, "depth": "quick",
        })
    assert response.status_code == 200, response.text
    names = {t["function"]["name"] for t in provider.complete_with_tools.call_args.kwargs["tools"]}
    assert names == {"list_tables", "get_table_schema", "get_table_rows", "query_table", "aggregate_table"}


def test_research_depth_is_the_default_and_related_can_be_disabled(monkeypatch):
    provider = Mock()
    provider.supports_native_tools.return_value = True
    provider.complete_with_tools.return_value = {"text": "Answer.", "tool_calls": [], "usage": {}}
    provider.complete.return_value = {"text": '["Should not be asked?"]', "usage": {}}
    _wire(monkeypatch, provider)

    with _client() as client:
        response = client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "Q"}],
            "provider": "ollama", "use_cache": False, "related": False,
        })
    body = response.json()
    assert body["depth"] == "research"
    assert body["related_questions"] == []
    provider.complete.assert_not_called()
    call = provider.complete_with_tools.call_args.kwargs
    assert len(call["tools"]) > 5 and "research_documents" in call["system"]


def test_quick_depth_on_the_prose_fallback_runs_one_pass_without_tool_protocol(monkeypatch):
    provider = Mock()
    provider.supports_native_tools.return_value = False
    provider.complete.side_effect = [
        {"text": "Plain answer. [Source 1]", "usage": {}},
        {"text": '["Next?"]', "usage": {}},
    ]
    _wire(monkeypatch, provider)

    with _client() as client:
        response = client.post("/api/chat/stream", json={
            "messages": [{"role": "user", "content": "Q"}],
            "provider": "ollama", "use_cache": False, "depth": "quick",
        })
    events = _events(response)
    assert not [e for e in events if e["type"] == "error"], events
    assert next(e for e in events if e["type"] == "done")["related_questions"] == ["Next?"]
    assert provider.complete.call_count == 2
    first_prompt = provider.complete.call_args_list[0].kwargs["prompt"]
    assert "TOOL USE PROTOCOL" not in first_prompt and "<tool_call>" not in first_prompt


def test_followup_failure_never_fails_the_turn(monkeypatch):
    provider = Mock()
    provider.supports_native_tools.return_value = True
    provider.complete_with_tools.return_value = {"text": "Answer.", "tool_calls": [], "usage": {}}
    provider.complete.side_effect = RuntimeError("quota")
    _wire(monkeypatch, provider)

    with _client() as client:
        response = client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "Q"}], "provider": "ollama", "use_cache": False,
        })
    assert response.status_code == 200
    assert response.json()["message"]["content"] == "Answer."
    assert response.json()["related_questions"] == []


def test_cached_answers_carry_their_followups(monkeypatch):
    monkeypatch.setattr(chat, "_cache_context", lambda *a: {"scope_key": "test"})
    monkeypatch.setattr(chat, "_cache_lookup", lambda ctx, threshold=None: {
        "answer": "Cached.", "question": "Q", "related": ["Then what?"],
        "sources": [{"filename": "a.pdf", "page_number": 1, "text_snippet": "x", "similarity_score": 0.9,
                     "document_id": "d", "collection_id": "default", "sensitivity": "public"}],
    })
    monkeypatch.setattr(chat, "record_chat_usage", lambda *a, **k: None)
    with _client() as client:
        sync = client.post("/api/chat", json={"messages": [{"role": "user", "content": "Q"}], "provider": "ollama"})
        stream = client.post("/api/chat/stream", json={"messages": [{"role": "user", "content": "Q"}], "provider": "ollama"})
    assert sync.json()["cached"] is True and sync.json()["related_questions"] == ["Then what?"]
    events = _events(stream)
    assert [e["questions"] for e in events if e["type"] == "related"] == [["Then what?"]]
    assert next(e for e in events if e["type"] == "done")["related_questions"] == ["Then what?"]


def test_depth_is_part_of_the_cache_fingerprint(monkeypatch):
    from models.schemas import ChatMessage, ChatRequest
    monkeypatch.setattr(chat, "_corpus_version", lambda cid: "v1")
    monkeypatch.setattr(chat, "_collection_guidance_version", lambda cid: "g1")
    quick = ChatRequest(messages=[ChatMessage(role="user", content="q")], depth="quick")
    research = ChatRequest(messages=[ChatMessage(role="user", content="q")])
    assert chat._request_fingerprint(quick, ["default"], "m") != chat._request_fingerprint(research, ["default"], "m")


# ── Starter questions ─────────────────────────────────────────────────────


def _starter_indexer(docs, chunk_text="Equipment must be returned within five days."):
    metadata = SimpleNamespace(
        get_document_chunk_rowids=lambda doc_id: [1] if doc_id != "empty" else [],
        get_chunks_by_rowids=lambda ids: {1: {"text": chunk_text}} if ids else {},
        corpus_version=lambda: "v1",
    )
    return SimpleNamespace(list_documents=lambda: docs, vector_store=SimpleNamespace(metadata_store=metadata))


def test_starters_sample_visible_documents_and_cache_per_corpus_version(monkeypatch):
    chat._STARTERS_CACHE.clear()
    provider = Mock()
    provider.complete.return_value = {"text": '["What is the return deadline?", "Who approves exceptions?"]', "usage": {"input_tokens": 4, "output_tokens": 2, "model": "fast"}}
    docs = [
        {"document_id": "a", "filename": "policy.pdf"},
        {"document_id": "q", "filename": "secret.pdf", "policy_status": "quarantined"},
        {"document_id": "empty", "filename": "empty.csv"},
    ]
    indexer = _starter_indexer(docs)
    monkeypatch.setattr(chat, "get_indexer", lambda *a: indexer)
    monkeypatch.setattr(chat, "create_provider", lambda *a, **k: provider)
    monkeypatch.setattr(chat, "AIService", lambda **k: SimpleNamespace(fast_model="fast"))
    monkeypatch.setattr(chat, "_build_collection_overview", lambda *a: "One collection")
    monkeypatch.setattr(chat, "check_daily_budget", lambda *a: None)
    usage = Mock()
    monkeypatch.setattr(chat, "record_chat_usage", usage)

    with _client() as client:
        first = client.post("/api/chat/starters?collection_id=default", json={"provider": "ollama"})
        second = client.post("/api/chat/starters?collection_id=default", json={"provider": "ollama"})
        selected = client.post("/api/chat/starters?collection_id=default", json={"provider": "ollama", "document_ids": ["a"]})
    assert first.status_code == 200, first.text
    assert first.json() == {"questions": ["What is the return deadline?", "Who approves exceptions?"], "cached": False}
    assert second.json()["cached"] is True
    assert selected.json()["cached"] is False  # a different selection is a different key
    assert provider.complete.call_count == 2
    prompt = provider.complete.call_args_list[0].kwargs["prompt"]
    assert "policy.pdf" in prompt and "secret.pdf" not in prompt
    assert usage.call_count == 2


def test_starters_missing_collection_and_empty_collection(monkeypatch):
    chat._STARTERS_CACHE.clear()

    def missing(*a):
        raise ValueError("Collection 'nope' not found")

    monkeypatch.setattr(chat, "get_indexer", missing)
    with _client() as client:
        assert client.post("/api/chat/starters?collection_id=nope", json={"provider": "ollama"}).status_code == 404

    monkeypatch.setattr(chat, "get_indexer", lambda *a: _starter_indexer([]))
    monkeypatch.setattr(chat, "create_provider", lambda *a, **k: Mock())
    monkeypatch.setattr(chat, "AIService", lambda **k: SimpleNamespace(fast_model="fast"))
    monkeypatch.setattr(chat, "check_daily_budget", lambda *a: None)
    with _client() as client:
        response = client.post("/api/chat/starters?collection_id=default", json={"provider": "ollama"})
    assert response.json() == {"questions": [], "cached": False}
