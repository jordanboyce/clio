"""A4: one answered question records the time spent in every stage, in a log
line, in /metrics and (for the search) in the ``retrieval`` block."""

import logging
from unittest.mock import MagicMock, Mock

import numpy as np

from api import chat
from models.schemas import ChunkMetadata
from services import stage_timing
from services.indexing.indexer import DocumentIndexer
from services.vector_store import VectorStore
from tests.test_chat_followups import _client, _events, _wire


class _Embedder:
    model_name = "test-embedder"
    embedding_dim = 16

    def _vec(self, text):
        v = np.zeros(self.embedding_dim, dtype="float32")
        for word in text.lower().split():
            v[sum(map(ord, word)) % self.embedding_dim] += 1.0
        return v / (np.linalg.norm(v) or 1.0)

    def embed_texts(self, texts, **_):
        return np.stack([self._vec(t) for t in texts])

    def embed_query(self, text, **_):
        return self._vec(text)


class _Reranker:
    def rerank_with_status(self, query, items, top_k, score_sink=None):
        return list(items)[:top_k], True


def _indexer(tmp_path):
    store = VectorStore(tmp_path / "index", embedding_dim=_Embedder.embedding_dim)
    texts = ["Claims are paid within five days.", "Visitors sign in at reception.",
             "Backups run every night at two."]
    chunks = [ChunkMetadata(chunk_id=f"d#{i}", document_id="d", filename="policy.pdf", page_number=i,
                            chunk_index=0, text=t, source_format="pdf", extraction_method="text")
              for i, t in enumerate(texts, start=1)]
    embedder = _Embedder()
    store.add_chunks(chunks, embedder.embed_texts(texts))
    return DocumentIndexer(vector_store=store, embedding_service=embedder,
                           document_extractor=MagicMock(), text_chunker=MagicMock())


def _count(stage):
    from services.metrics import registry
    return registry.get_sample_value("clio_stage_duration_seconds_count", {"stage": stage}) or 0.0


def test_a_search_reports_its_stages(tmp_path, monkeypatch):
    monkeypatch.setattr("services.reranker.get_reranker", lambda: _Reranker())

    out = _indexer(tmp_path).search("how long are claims paid", top_k=2)

    timings = out["retrieval"]["timings_ms"]
    assert {"embed_query", "vector_search", "keyword_search", "rerank"} <= set(timings)
    assert all(v >= 0 for v in timings.values())


def test_an_answer_logs_and_counts_every_stage(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr("services.reranker.get_reranker", lambda: _Reranker())
    provider = Mock()
    provider.supports_native_tools.return_value = True
    provider.complete_with_tools.return_value = {"text": "Five days [Source 1].", "tool_calls": [], "usage": {}}
    _wire(monkeypatch, provider)
    indexer = _indexer(tmp_path)
    monkeypatch.setattr(chat, "get_indexer", lambda *a: indexer)
    stages = ("embed_query", "vector_search", "keyword_search", "rerank", "model_call", "first_text", "answer")
    before = {s: _count(s) for s in stages}

    with caplog.at_level(logging.INFO, logger=chat.logger.name), _client() as client:
        response = client.post("/api/chat/stream", json={
            "messages": [{"role": "user", "content": "How long are claims paid?"}],
            "provider": "ollama", "use_cache": False, "depth": "quick", "related": False})

    assert [e["type"] for e in _events(response)][-1] == "done"
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Answer timings"))
    for s in stages:
        assert f"{s} " in line, (s, line)
        assert _count(s) > before[s], s


def test_a_nested_trace_adds_to_the_outer_one():
    with stage_timing.trace() as outer:
        stage_timing.observe("rerank", 0.002)
        with stage_timing.trace() as inner:
            stage_timing.observe("rerank", 0.003)
    assert round(inner["rerank"], 3) == 3.0
    assert round(outer["rerank"], 3) == 5.0
