"""Semantic answer cache: similarity lookup, invalidation, and chat wiring."""

from types import SimpleNamespace

import numpy as np
import pytest

import config
import services.answer_cache as ac_module
from services.answer_cache import AnswerCache


V_A = np.array([1.0, 0.0, 0.0], dtype=np.float32)
V_A_CLOSE = np.array([0.98, 0.1, 0.0], dtype=np.float32)   # cos ≈ 0.995
V_B = np.array([0.0, 1.0, 0.0], dtype=np.float32)          # cos = 0


@pytest.fixture()
def cache(tmp_path):
    return AnswerCache(db_path=tmp_path / "cache.db")


def _store(cache, vec=V_A, scope="col:default", model="mini", question="what is x?",
           answer="x is 42", fingerprints=None):
    return cache.store(
        scope, model, question, vec, answer,
        sources=[{"filename": "a.pdf", "page_number": 1, "text_snippet": "x",
                  "similarity_score": 0.9, "document_id": "doc1", "collection_id": "default"}],
        fingerprints=fingerprints if fingerprints is not None else [
            {"collection_id": "default", "document_id": "doc1",
             "filename": "a.pdf", "upload_timestamp": "t1", "num_chunks": 3, "content_hash": "hash-a", "sensitivity": "internal"}
        ],
        provider="ollama",
    )


def test_hit_above_threshold(cache):
    _store(cache)
    entry = cache.find_best("col:default", "mini", V_A_CLOSE, threshold=0.9)
    assert entry is not None
    assert entry["answer"] == "x is 42"
    assert entry["similarity"] >= 0.99


def test_miss_below_threshold_and_wrong_scope(cache):
    _store(cache)
    assert cache.find_best("col:default", "mini", V_B, threshold=0.9) is None
    assert cache.find_best("col:other", "mini", V_A, threshold=0.9) is None
    assert cache.find_best("col:default", "other-model", V_A, threshold=0.9) is None


def test_near_duplicate_store_replaces(cache):
    _store(cache, answer="old answer")
    _store(cache, vec=V_A, answer="new answer")
    entry = cache.find_best("col:default", "mini", V_A, threshold=0.9)
    assert entry["answer"] == "new answer"
    assert cache.stats()["entries"] == 1


def test_lru_cap_evicts_oldest(cache, monkeypatch):
    monkeypatch.setattr(config.settings, "answer_cache_max_per_scope", 2)
    _store(cache, vec=V_A, question="q1")
    _store(cache, vec=V_B, question="q2")
    v3 = np.array([0.0, 0.0, 1.0], dtype=np.float32)
    _store(cache, vec=v3, question="q3")
    assert cache.stats()["entries"] == 2
    assert cache.find_best("col:default", "mini", v3, threshold=0.9) is not None


def test_clear(cache):
    _store(cache)
    assert cache.clear() == 1
    assert cache.stats()["entries"] == 0


# ── Chat-layer wiring ───────────────────────────────────────────────────────


class _FakeEmbedder:
    model_name = "mini"

    def embed_query(self, q):
        return V_A


@pytest.fixture()
def chat_env(cache, monkeypatch):
    """Point the chat helpers at a temp cache and a fake local embedder."""
    import api.chat as chat

    monkeypatch.setattr(ac_module, "answer_cache", cache)
    monkeypatch.setattr(
        chat, "get_indexer", lambda cid="default": SimpleNamespace(embedding_service=_FakeEmbedder())
    )
    monkeypatch.setattr(config.settings, "enable_answer_cache", True)
    monkeypatch.setattr(config.settings, "answer_cache_threshold", 0.9)
    return chat


def _request(messages=None, scope="current", use_cache=True):
    from models.schemas import ChatMessage, ChatRequest

    return ChatRequest(
        messages=messages or [ChatMessage(role="user", content="what is x?")],
        provider="ollama",
        scope=scope,
        use_cache=use_cache,
    )


def test_cache_context_single_turn_only(chat_env):
    from models.schemas import ChatMessage

    ctx = chat_env._cache_context(_request(), "default")
    # "<scope>#<request fingerprint>": the scope names the collection, the
    # fingerprint pins everything else that shapes the answer.
    assert ctx and ctx["scope_key"].startswith("col:default#") and ctx["embedding_model"] == "mini"
    assert len(ctx["scope_key"].split("#", 1)[1]) == 16

    multi = _request(messages=[
        ChatMessage(role="user", content="what is x?"),
        ChatMessage(role="assistant", content="42"),
        ChatMessage(role="user", content="why?"),
    ])
    assert chat_env._cache_context(multi, "default") is None


def test_cache_context_disabled(chat_env, monkeypatch):
    monkeypatch.setattr(config.settings, "enable_answer_cache", False)
    assert chat_env._cache_context(_request(), "default") is None


def test_lookup_serves_fresh_entry(chat_env, cache, monkeypatch):
    _store(cache, scope=chat_env._cache_context(_request(), "default")["scope_key"])
    monkeypatch.setattr(
        chat_env, "_document_fingerprint",
        lambda cid, did: {"filename": "a.pdf", "upload_timestamp": "t1", "num_chunks": 3, "content_hash": "hash-a", "sensitivity": "internal"},
    )
    ctx = chat_env._cache_context(_request(), "default")
    entry = chat_env._cache_lookup(ctx)
    assert entry is not None and entry["answer"] == "x is 42"


def test_lookup_discards_entry_when_source_changed(chat_env, cache, monkeypatch):
    _store(cache, scope=chat_env._cache_context(_request(), "default")["scope_key"])
    # Document re-uploaded since the answer was generated
    monkeypatch.setattr(
        chat_env, "_document_fingerprint",
        lambda cid, did: {"filename": "a.pdf", "upload_timestamp": "t2-CHANGED", "num_chunks": 3, "content_hash": "hash-a", "sensitivity": "internal"},
    )
    ctx = chat_env._cache_context(_request(), "default")
    assert chat_env._cache_lookup(ctx) is None
    assert cache.stats()["entries"] == 0  # stale entry was deleted, not just skipped


def test_lookup_discards_entry_when_source_deleted(chat_env, cache, monkeypatch):
    _store(cache, scope=chat_env._cache_context(_request(), "default")["scope_key"])
    monkeypatch.setattr(chat_env, "_document_fingerprint", lambda cid, did: None)
    ctx = chat_env._cache_context(_request(), "default")
    assert chat_env._cache_lookup(ctx) is None


def test_similar_questions_do_not_reuse_a_different_answer(chat_env, cache, monkeypatch):
    _store(cache, question="what was revenue in 2024?",
           scope=chat_env._cache_context(_request(), "default")["scope_key"])
    monkeypatch.setattr(chat_env, "_document_fingerprint", lambda cid, did: {
        "filename": "a.pdf", "upload_timestamp": "t1", "num_chunks": 3,
        "content_hash": "hash-a", "sensitivity": "internal",
    })
    # The fake embedder gives both questions IDENTICAL vectors; the answer
    # must still be regenerated because the requested year changed.
    from models.schemas import ChatMessage
    ctx = chat_env._cache_context(_request(messages=[
        ChatMessage(role="user", content="what was revenue in 2025?")
    ]), "default")
    assert chat_env._cache_lookup(ctx) is None


FRESH_FP = {
    "filename": "a.pdf", "upload_timestamp": "t1", "num_chunks": 3,
    "content_hash": "hash-a", "sensitivity": "internal",
}


def _ask(text):
    from models.schemas import ChatMessage
    return _request(messages=[ChatMessage(role="user", content=text)])


def test_rephrased_question_reuses_the_answer(chat_env, cache, monkeypatch):
    _store(cache, question="what is x?", scope=chat_env._cache_context(_request(), "default")["scope_key"])
    monkeypatch.setattr(chat_env, "_document_fingerprint", lambda cid, did: FRESH_FP)
    ctx = chat_env._cache_context(_ask("What's x, exactly?"), "default")
    entry = chat_env._cache_lookup(ctx)
    assert entry is not None and entry["answer"] == "x is 42"


def test_negated_question_is_a_different_question(chat_env, cache, monkeypatch):
    _store(cache, question="is x permitted?", scope=chat_env._cache_context(_request(), "default")["scope_key"])
    monkeypatch.setattr(chat_env, "_document_fingerprint", lambda cid, did: FRESH_FP)
    assert chat_env._cache_lookup(chat_env._cache_context(_ask("is x not permitted?"), "default")) is None
    assert chat_env._cache_lookup(chat_env._cache_context(_ask("isn't x permitted?"), "default")) is None
    assert chat_env._cache_lookup(chat_env._cache_context(_ask("Is X permitted ?"), "default")) is not None


def test_exact_threshold_reuses_only_identical_text(chat_env, cache, monkeypatch):
    _store(cache, question="what is x?", scope=chat_env._cache_context(_request(), "default")["scope_key"])
    monkeypatch.setattr(chat_env, "_document_fingerprint", lambda cid, did: FRESH_FP)
    assert chat_env._cache_lookup(chat_env._cache_context(_ask("What's x, exactly?"), "default"), 1.0) is None
    assert chat_env._cache_lookup(chat_env._cache_context(_ask("  What is  X? "), "default"), 1.0) is not None


def test_request_threshold_overrides_the_default(chat_env, cache, monkeypatch):
    # Stored under V_A; the asked question embeds to V_A_CLOSE (cos ≈ 0.995).
    _store(cache, vec=V_A, question="what is x?", scope=chat_env._cache_context(_request(), "default")["scope_key"])
    monkeypatch.setattr(chat_env, "_document_fingerprint", lambda cid, did: FRESH_FP)
    monkeypatch.setattr(_FakeEmbedder, "embed_query", lambda self, q: V_A_CLOSE)
    ctx = chat_env._cache_context(_ask("what is x really?"), "default")
    assert chat_env._cache_lookup(ctx) is not None          # deployment default 0.9
    assert chat_env._cache_lookup(ctx, 0.999) is None       # stricter per-request floor
    assert chat_env._cache_lookup(ctx, 0.95) is not None


def test_chat_request_bounds_cache_threshold():
    from pydantic import ValidationError
    from models.schemas import ChatMessage, ChatRequest

    msgs = [ChatMessage(role="user", content="q")]
    assert ChatRequest(messages=msgs).cache_threshold is None
    assert ChatRequest(messages=msgs, cache_threshold=0.95).cache_threshold == 0.95
    with pytest.raises(ValidationError):
        ChatRequest(messages=msgs, cache_threshold=0.2)
    with pytest.raises(ValidationError):
        ChatRequest(messages=msgs, cache_threshold=1.5)


def test_cache_refuses_sources_without_fingerprints(chat_env, cache):
    _store(cache, fingerprints=[], scope=chat_env._cache_context(_request(), "default")["scope_key"])
    ctx = chat_env._cache_context(_request(), "default")
    assert chat_env._cache_lookup(ctx) is None
    assert cache.stats()["entries"] == 0


def test_store_skips_sourceless_answers(chat_env, cache):
    ctx = chat_env._cache_context(_request(), "default")
    chat_env._cache_store(ctx, "no idea", [], "ollama")
    assert cache.stats()["entries"] == 0


def test_store_then_lookup_roundtrip(chat_env, cache, monkeypatch):
    fp = {"filename": "b.pdf", "upload_timestamp": "t9", "num_chunks": 7, "content_hash": "hash-b", "sensitivity": "internal"}
    monkeypatch.setattr(chat_env, "_document_fingerprint", lambda cid, did: dict(fp))
    result = SimpleNamespace(
        filename="b.pdf", page_number=2, text_snippet="snippet",
        similarity_score=0.8, document_id="doc9",
    )
    ctx = chat_env._cache_context(_request(), "default")
    chat_env._cache_store(ctx, "the answer", [(result, "default")], "ollama")

    entry = chat_env._cache_lookup(ctx)
    assert entry["answer"] == "the answer"
    assert entry["sources"][0]["collection_id"] == "default"
    # URL rebuild uses the current request's host
    payload = chat_env._cached_sources(entry, "http://x")
    assert payload[0]["pdf_url"].startswith("http://x/documents/doc9/pdf?collection_id=default")


# ── The request fingerprint: caching reflects the entire request ─────────


def _key(chat_env, **kwargs):
    model = kwargs.pop("model", "")
    return chat_env._cache_context(_request(**kwargs), "default", model)["scope_key"]


def test_fingerprint_changes_with_model_and_retrieval_settings(chat_env):
    from models.schemas import ChatMessage, ChatRequest, SearchMode

    base = _key(chat_env)
    assert _key(chat_env) == base                       # deterministic
    assert _key(chat_env, model="llama3.2@") != base    # different model
    assert chat_env._cache_context(
        ChatRequest(messages=[ChatMessage(role="user", content="what is x?")], provider="anthropic"),
        "default",
    )["scope_key"] != base                              # different provider
    assert chat_env._cache_context(
        ChatRequest(messages=[ChatMessage(role="user", content="what is x?")], provider="ollama", top_k=9),
        "default",
    )["scope_key"] != base                              # different top_k
    assert chat_env._cache_context(
        ChatRequest(messages=[ChatMessage(role="user", content="what is x?")], provider="ollama",
                    mode=SearchMode.HYBRID),
        "default",
    )["scope_key"] != base                              # different mode
    assert chat_env._cache_context(
        ChatRequest(messages=[ChatMessage(role="user", content="what is x?")], provider="ollama",
                    document_ids=["doc1"]),
        "default",
    )["scope_key"] != base                              # a source selection


def test_fingerprint_changes_when_corpus_or_guidance_changes(chat_env, monkeypatch):
    base = _key(chat_env)
    monkeypatch.setattr(chat_env, "_corpus_version", lambda cid: "2:2026-09-12T00:00:00:40:0")
    after_upload = _key(chat_env)
    assert after_upload != base
    monkeypatch.setattr(chat_env, "_collection_guidance_version", lambda cid: "guide-v2")
    assert _key(chat_env) != after_upload


def test_clearing_a_scope_drops_every_fingerprint(chat_env, cache):
    _store(cache, scope="col:default#aaaa")
    _store(cache, scope="col:default#bbbb", question="other?")
    _store(cache, scope="col:other#cccc", question="third?")
    assert cache.clear("col:default") == 2
    assert cache.stats()["entries"] == 1


def test_cache_status_endpoint_reports_default_threshold(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import api.chat as chat

    monkeypatch.setattr(config.settings, "enable_answer_cache", True)
    monkeypatch.setattr(config.settings, "answer_cache_threshold", 0.87)
    app = FastAPI()
    app.include_router(chat.router)
    with TestClient(app) as client:
        body = client.get("/api/chat/cache").json()
    assert body == {"enabled": True, "threshold": 0.87}
