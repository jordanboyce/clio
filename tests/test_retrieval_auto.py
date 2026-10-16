"""Automatic retrieval: the planner, the indexer's ``auto`` mode, the reranker default."""

from unittest.mock import MagicMock

import numpy as np
import pytest

from models.schemas import ChunkMetadata, SearchMode
from services.retrieval_planner import (
    KEYWORD_LED,
    KEYWORD_LEANING,
    MEANING_LED,
    plan_retrieval,
)


# ── Planner ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("query", [
    "ERR_4021",
    "CVE-2024-1234",
    "v2.3.1",
    "config.yaml",
    "getUserById",
    "/etc/nginx/nginx.conf",
    "ticket 100234",
    "ERR4021 timeout",          # identifier is half the query
])
def test_identifiers_lead_with_keywords(query):
    plan = plan_retrieval(query)
    assert plan.mode == SearchMode.HYBRID
    assert plan.semantic_weight == KEYWORD_LED
    assert plan.reason == "exact identifier"


def test_quoted_phrase_leads_with_keywords():
    plan = plan_retrieval('what does "retention period" mean for backups')
    assert plan.semantic_weight == KEYWORD_LED
    assert plan.reason == "quoted phrase"


def test_identifier_inside_a_longer_query_only_leans():
    plan = plan_retrieval("why does the importer fail with ERR_4021 on large files")
    assert plan.semantic_weight == KEYWORD_LEANING
    assert plan.reason == "identifier in a longer query"


@pytest.mark.parametrize("query", [
    "How do we handle customer refunds?",
    "what is the escalation process",
    "explain the onboarding flow for new hires",
    "the policy on remote work during the holiday season",   # six words, no '?'
])
def test_questions_lead_with_meaning(query):
    plan = plan_retrieval(query)
    assert plan.semantic_weight >= MEANING_LED
    assert plan.reason == "natural-language question"


def test_bare_years_and_small_numbers_are_ordinary_words():
    # A year is prose, not an identifier that must match literally.
    plan = plan_retrieval("revenue growth in 2024")
    assert plan.reason != "exact identifier"
    assert plan.reason != "identifier in a longer query"


def test_short_terms_lean_on_keywords():
    plan = plan_retrieval("retention policy")
    assert plan.semantic_weight == KEYWORD_LEANING
    assert plan.reason == "short term"


def test_in_between_queries_keep_the_configured_balance():
    plan = plan_retrieval("backup schedule for databases", base_weight=0.6)
    assert plan.semantic_weight == 0.6
    assert plan.reason == "default balance"


def test_planner_always_returns_hybrid_and_a_valid_weight():
    for q in ["", "   ", "?", "a", "x" * 500, '"', "1 2 3 4 5 6 7"]:
        plan = plan_retrieval(q)
        assert plan.mode == SearchMode.HYBRID
        assert 0.0 <= plan.semantic_weight <= 1.0


# ── Indexer: auto resolves, reports, and degrades ────────────────────────

class _Embedding:
    def __init__(self, fail=False, dim=384):
        self.fail = fail
        self.dim = dim

    def embed_query(self, _text):
        if self.fail:
            from services.embedder import EmbeddingUnavailable
            raise EmbeddingUnavailable("model not seeded")
        v = np.ones(self.dim, dtype=np.float32)
        return v / np.linalg.norm(v)

    def embed_texts(self, texts):
        v = np.ones(self.dim, dtype=np.float32)
        v = v / np.linalg.norm(v)
        return np.tile(v, (len(texts), 1))

    def describe_embedding(self):
        return {"provider": "test", "model": "constant", "dim": self.dim}


def _indexer(tmp_path, embedding):
    from services.indexing.indexer import DocumentIndexer
    from services.vector_store import VectorStore

    store = VectorStore(tmp_path / "index")
    indexer = DocumentIndexer(
        vector_store=store,
        embedding_service=embedding,
        document_extractor=MagicMock(),
        text_chunker=MagicMock(),
    )
    docs = {
        "a": "The importer raised ERR_4021 while reading the archive.",
        "b": "Customers can request a refund within thirty days of purchase.",
    }
    for doc_id, text in docs.items():
        chunk = ChunkMetadata(chunk_id=f"{doc_id}-c0", document_id=doc_id,
                              filename=f"{doc_id}.txt", page_number=1, chunk_index=0, text=text)
        store.add_chunks([chunk], _Embedding().embed_texts([text]))
    return indexer


def test_auto_is_the_default_and_reports_what_it_did(tmp_path):
    indexer = _indexer(tmp_path, _Embedding())
    out = indexer.search("ERR_4021", top_k=2)
    info = out["retrieval"]
    assert info["requested"] == "auto"
    assert info["mode"] == "hybrid"
    assert info["semantic_weight"] == KEYWORD_LED
    assert info["reason"] == "exact identifier"
    assert out["results"][0].chunk_id == "a-c0"


def test_forced_mode_is_untouched_by_the_planner(tmp_path):
    indexer = _indexer(tmp_path, _Embedding())
    out = indexer.search("ERR_4021", top_k=2, mode=SearchMode.KEYWORD)
    assert out["retrieval"]["requested"] == "keyword"
    assert out["retrieval"]["mode"] == "keyword"
    assert out["retrieval"]["semantic_weight"] is None


def test_auto_falls_back_to_keywords_when_embeddings_are_unavailable(tmp_path):
    indexer = _indexer(tmp_path, _Embedding())
    indexer.embedding_service = _Embedding(fail=True)
    out = indexer.search("ERR_4021", top_k=2)
    assert out["retrieval"]["mode"] == "keyword"
    assert "unavailable" in out["retrieval"]["reason"]
    assert out["results"][0].chunk_id == "a-c0"


def test_forced_semantic_still_raises_without_embeddings(tmp_path):
    from services.embedder import EmbeddingUnavailable

    indexer = _indexer(tmp_path, _Embedding())
    indexer.embedding_service = _Embedding(fail=True)
    with pytest.raises(EmbeddingUnavailable):
        indexer.search("ERR_4021", top_k=2, mode=SearchMode.SEMANTIC)


# ── Reranker default ─────────────────────────────────────────────────────

def test_reranker_unset_follows_local_availability(monkeypatch):
    from config import settings
    from services import reranker

    monkeypatch.setattr(settings, "enable_reranker", None, raising=False)
    monkeypatch.setattr(reranker, "_reranker", None)

    monkeypatch.setattr(reranker, "_locally_available", lambda name: False)
    assert reranker.get_reranker() is None

    monkeypatch.setattr(reranker, "_locally_available", lambda name: True)
    assert reranker.get_reranker() is not None


def test_reranker_explicit_false_wins_over_availability(monkeypatch):
    from config import settings
    from services import reranker

    monkeypatch.setattr(settings, "enable_reranker", False, raising=False)
    monkeypatch.setattr(reranker, "_locally_available", lambda name: True)
    assert reranker.get_reranker() is None


def test_reranker_availability_never_downloads(monkeypatch):
    """With the model absent from the cache the check says no and stays offline."""
    from services import reranker

    monkeypatch.setattr(reranker, "_auto_cache", {})
    calls = []

    def fake_snapshot_download(name, **kwargs):
        calls.append(kwargs)
        raise FileNotFoundError("not cached")

    monkeypatch.setitem(__import__("sys").modules, "huggingface_hub",
                        MagicMock(snapshot_download=fake_snapshot_download))
    monkeypatch.setattr("importlib.util.find_spec", lambda name: object())
    assert reranker._locally_available("cross-encoder/none") is False
    assert calls and all(c.get("local_files_only") is True for c in calls)


# ── Keyword index: identifiers must be findable ──────────────────────────

def test_tokenizer_splits_identifiers_instead_of_dropping_them():
    from services.bm25_service import BM25Index

    tokens = BM25Index._tokenize("ERR_4021 in get_user_by_id, café 数据")
    assert {"err", "4021", "get", "user", "id", "café"} <= set(tokens)
    assert any("数据" in t for t in tokens)


def test_keyword_search_finds_an_underscore_identifier(tmp_path):
    indexer = _indexer(tmp_path, _Embedding())
    out = indexer.search("ERR_4021", top_k=2, mode=SearchMode.KEYWORD)
    assert [r.chunk_id for r in out["results"]] == ["a-c0"]


def test_index_built_by_the_old_tokenizer_is_rebuilt_on_open(tmp_path):
    import sqlite3

    from services.vector_store import VectorStore

    indexer = _indexer(tmp_path, _Embedding())
    bm25 = indexer.vector_store.bm25_index
    # Simulate an index written before the tokenizer fix: the identifier chunk
    # is missing from the keyword index and no tokenizer version is recorded.
    bm25.clear()
    bm25.add_document("b-c0", "Customers can request a refund within thirty days of purchase.")
    with sqlite3.connect(bm25.db_path) as conn:
        conn.execute("DELETE FROM bm25_meta WHERE key = 'tokenizer_version'")
    assert bm25.search("err 4021") == []

    reopened = VectorStore(tmp_path / "index")
    assert [cid for cid, _ in reopened.bm25_index.search("ERR_4021")] == ["a-c0"]
    assert reopened.bm25_index.needs_rebuild is False
    # Both chunks are back, and a second open does not rebuild again.
    assert {cid for cid, _ in reopened.bm25_index.search("refund")} == {"b-c0"}
    assert VectorStore(tmp_path / "index").bm25_index.needs_rebuild is False


def test_a_failed_rebuild_keeps_the_old_keyword_index(tmp_path, monkeypatch):
    """The rebuild runs on existing user data: a failure part-way must leave the
    index it found, not an empty one, and still be retried on the next open."""
    import sqlite3

    from services.bm25_service import BM25Index

    bm25 = BM25Index(tmp_path / "bm25.db")
    bm25.add_document("c1", "Customers can request a refund within thirty days.")
    with sqlite3.connect(bm25.db_path) as conn:
        conn.execute("DELETE FROM bm25_meta WHERE key = 'tokenizer_version'")

    def boom(self, conn, documents):
        raise RuntimeError("disk full")

    monkeypatch.setattr(BM25Index, "_insert_documents", boom)
    with pytest.raises(RuntimeError):
        bm25.rebuild([("c1", "Customers can request a refund within thirty days."), ("c2", "ERR_4021")])

    assert [cid for cid, _ in bm25.search("refund")] == ["c1"]
    assert BM25Index(tmp_path / "bm25.db").needs_rebuild is True
