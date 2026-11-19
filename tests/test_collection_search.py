"""B1: asking across collections keeps the reranker's order.

Each collection used to be searched and reranked on its own, then the lists
were merged on the first-stage score, which the reranker never changes and
which is not comparable between collections."""

import contextvars
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np

from models.schemas import ChunkMetadata
from services import collection_search
from services.indexing.indexer import DocumentIndexer
from services.vector_store import VectorStore


def hit(name, first_stage, rerank=None):
    return SimpleNamespace(chunk_id=name, similarity_score=first_stage, rerank_score=rerank)


def test_reranked_results_are_merged_on_the_reranker_score():
    merged = collection_search.merge([
        ("notes", [hit("notes-1", 0.95, rerank=0.10)]),
        ("policies", [hit("policy-1", 0.40, rerank=0.90), hit("policy-2", 0.35, rerank=0.20)]),
    ], top_k=3)

    assert [(r.chunk_id, cid) for r, cid in merged] == [
        ("policy-1", "policies"), ("policy-2", "policies"), ("notes-1", "notes")]


def test_without_a_reranker_the_first_stage_score_decides():
    # Merging by rank was measured and lost (see services/collection_search.py).
    merged = collection_search.merge([
        ("a", [hit("a-1", 0.99), hit("a-2", 0.98), hit("a-3", 0.97)]),
        ("b", [hit("b-1", 0.30), hit("b-2", 0.20)]),
    ], top_k=4)

    assert [r.chunk_id for r, _ in merged] == ["a-1", "a-2", "a-3", "b-1"]


def test_one_collection_not_reranked_means_no_comparable_score():
    merged = collection_search.merge([
        ("a", [hit("a-1", 0.2, rerank=0.9)]),
        ("b", [hit("b-1", 0.6)]),            # shed under load: no score
    ], top_k=2)
    assert [r.chunk_id for r, _ in merged] == ["b-1", "a-1"]


class _Embedder:
    model_name = "test"
    embedding_dim = 32

    def _vec(self, text):
        v = np.zeros(self.embedding_dim, dtype="float32")
        for word in text.lower().replace(".", "").split():
            v[sum(map(ord, word)) % self.embedding_dim] += 1.0
        return v / (np.linalg.norm(v) or 1.0)

    def embed_texts(self, texts, **_):
        return np.stack([self._vec(t) for t in texts])

    def embed_query(self, text, **_):
        return self._vec(text)


class _Reranker:
    """Scores a passage by whether it holds the answer, as a cross-encoder would."""

    def rerank_with_status(self, query, items, top_k, score_sink=None):
        scores = {id(it): (4.0 if "30 days" in it.text_snippet else -4.0) for it in items}
        if score_sink is not None:
            score_sink.update(scores)
        return sorted(items, key=lambda it: scores[id(it)], reverse=True)[:top_k], True


def _collection(tmp_path, name, texts):
    store = VectorStore(tmp_path / name, embedding_dim=_Embedder.embedding_dim)
    chunks = [ChunkMetadata(chunk_id=f"{name}#{i}", document_id=name, filename=f"{name}.pdf", page_number=i,
                            chunk_index=0, text=t, source_format="pdf", extraction_method="text")
              for i, t in enumerate(texts, start=1)]
    store.add_chunks(chunks, _Embedder().embed_texts(texts))
    return name, DocumentIndexer(vector_store=store, embedding_service=_Embedder(),
                                 document_extractor=MagicMock(), text_chunker=MagicMock())


def test_the_better_passage_wins_although_its_first_stage_score_is_lower(tmp_path, monkeypatch):
    monkeypatch.setattr("services.reranker.get_reranker", lambda: _Reranker())
    question = "how long do refund claims take to pay"
    notes = _collection(tmp_path, "notes", [
        "Refund claims take long to pay, people said; how long is unclear.",
        "Minutes of the meeting.",
    ])
    policies = _collection(tmp_path, "policies", [
        "They are settled within 30 days of receipt.",
        "Visitors sign in at reception.",
    ])
    first_stage = {cid: ix.search(query=question, top_k=1)["results"][0] for cid, ix in (notes, policies)}
    assert first_stage["notes"].similarity_score > first_stage["policies"].similarity_score

    merged = collection_search.search_collections([notes, policies], question, top_k=3)

    assert merged[0][1] == "policies"
    assert "30 days" in merged[0][0].text_snippet


def test_collections_are_searched_in_parallel_in_the_callers_context():
    who = contextvars.ContextVar("who", default=None)
    seen = []

    class Indexer:
        def search(self, **kwargs):
            seen.append(who.get())
            return {"results": []}

    who.set("alice")
    collection_search.search_collections([("a", Indexer()), ("b", Indexer())], "q", top_k=3)

    assert seen == ["alice", "alice"]


def test_a_failing_collection_is_left_out_and_reported():
    class Broken:
        def search(self, **kwargs):
            raise RuntimeError("index missing")

    class Fine:
        def search(self, **kwargs):
            return {"results": [hit("x", 0.5)]}

    errors = []
    merged = collection_search.search_collections([("bad", Broken()), ("good", Fine())], "q", top_k=3,
                                                  on_error=lambda cid, e: errors.append(cid))
    assert [cid for _, cid in merged] == ["good"]
    assert errors == ["bad"]


def test_rerank_scores_stay_out_of_the_api():
    from models.schemas import SearchResult
    r = SearchResult(filename="f", page_number=1, text_snippet="t", similarity_score=0.5, document_id="d",
                     chunk_id="c", pdf_url="", page_url="", rerank_score=0.7)
    assert "rerank_score" not in r.model_dump()
    assert r.rerank_score == 0.7
